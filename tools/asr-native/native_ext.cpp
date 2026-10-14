// SPDX-License-Identifier: Apache-2.0
// Adapted from netease-youdao/Confucius4-R2T2 r2t2_llama/native_ext.cpp.
// See vendor/r2t2_native/NOTICE.md and LICENSE for provenance and terms.
#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <pybind11/stl.h>

#include "llama.h"
#include "ggml.h"
#include "mtmd.h"
#include "mtmd-helper.h"
#include "mtmd-helper-common.h"  // decode_embd_batch: project-internal mtmd helper used for external embeddings

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <numeric>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace py = pybind11;

namespace {

void private_worker_log(enum ggml_log_level level, const char * message, void *) {
    // mtmd INFO logs can contain the entire prompt and partial transcript.
    if (level == GGML_LOG_LEVEL_ERROR && message) {
        std::fputs(message, stderr);
    }
}

struct ModelDeleter {
    void operator()(llama_model * model) const {
        if (model) {
            llama_model_free(model);
        }
    }
};

struct ContextDeleter {
    void operator()(llama_context * ctx) const {
        if (ctx) {
            llama_free(ctx);
        }
    }
};

struct MtmdDeleter {
    void operator()(mtmd_context * ctx) const {
        if (ctx) {
            mtmd_free(ctx);
        }
    }
};

struct BitmapDeleter {
    void operator()(mtmd_bitmap * bitmap) const {
        if (bitmap) {
            mtmd_bitmap_free(bitmap);
        }
    }
};

struct ChunksDeleter {
    void operator()(mtmd_input_chunks * chunks) const {
        if (chunks) {
            mtmd_input_chunks_free(chunks);
        }
    }
};

struct SamplerDeleter {
    void operator()(llama_sampler * sampler) const {
        if (sampler) {
            llama_sampler_free(sampler);
        }
    }
};

using model_ptr   = std::unique_ptr<llama_model, ModelDeleter>;
using context_ptr = std::unique_ptr<llama_context, ContextDeleter>;
using mtmd_ptr    = std::unique_ptr<mtmd_context, MtmdDeleter>;
using bitmap_ptr  = std::unique_ptr<mtmd_bitmap, BitmapDeleter>;
using chunks_ptr  = std::unique_ptr<mtmd_input_chunks, ChunksDeleter>;
using sampler_ptr = std::unique_ptr<llama_sampler, SamplerDeleter>;

std::string replace_all(std::string value, const std::string & from, const std::string & to) {
    size_t pos = 0;
    while ((pos = value.find(from, pos)) != std::string::npos) {
        value.replace(pos, from.size(), to);
        pos += to.size();
    }
    return value;
}

// Optional conservative streaming rule copied from the hybrid implementation.
// With QWEN3ASR_STOP_BIAS unset (or <= 0), this is exactly greedy argmax.
float stop_bias_threshold() {
    static const float threshold = []() {
        const char * value = std::getenv("QWEN3ASR_STOP_BIAS");
        if (!value || !*value) {
            return 0.0f;
        }
        return static_cast<float>(std::atof(value));
    }();
    return threshold;
}

bool is_stop_like(llama_token token) {
    // 91 = '|', 2 = '#', 151645 = <|im_end|>
    return token == 91 || token == 2 || token == 151645;
}

llama_token pick_token_greedy(const float * logits, int32_t n_vocab) {
    // QWEN3ASR_STOP_BIAS 没有设置时，阈值为0，pick_token_greedy会完全退化为普通argmax，不会执行偏向停止token的逻辑
    // pick_token_greedy可以处理如下情况：
    // top1 = 普通文字 token
    // top2 = | 或 # 或 <|im_end|> (stop)
    // top1 与 top2 的 logit 差小于阈值
    // 且QWEN3ASR_STOP_BIAS=0.1
    // 则会选择top2
    llama_token best = 0;
    llama_token second = -1;
    for (int32_t i = 1; i < n_vocab; ++i) {
        if (logits[i] > logits[best]) {
            second = best;
            best = i;
        } else if (second < 0 || logits[i] > logits[second]) {
            second = i;
        }
    }
    const float threshold = stop_bias_threshold();
    if (threshold > 0.0f && second >= 0 && !is_stop_like(best) && is_stop_like(second) &&
            logits[best] - logits[second] < threshold) {
        return second;
    }
    return best;
}

std::string detokenize(const llama_vocab * vocab, const std::vector<llama_token> & tokens) {
    std::string result;
    char buffer[512];
    for (const llama_token token : tokens) {
        const int32_t n = llama_token_to_piece(vocab, token, buffer, sizeof(buffer), 0, false);
        if (n > 0) {
            result.append(buffer, static_cast<size_t>(n));
        }
    }
    return result;
}

uint64_t fnv1a_f32(const float * values, size_t count) {
    const auto * bytes = reinterpret_cast<const uint8_t *>(values);
    uint64_t hash = 1469598103934665603ULL;
    for (size_t i = 0; i < count * sizeof(float); ++i) {
        hash ^= bytes[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

bool debug_enabled() {
    const char * value = std::getenv("DEBUG_PRINT");
    return value != nullptr && (value[0] == '1' || value[0] == 'y' || value[0] == 'Y');
}

bool embed_bf16_enabled() {
    const char * value = std::getenv("QWEN3ASR_EMBED_BF16");
    return value != nullptr && (value[0] == '1' || value[0] == 'y' || value[0] == 'Y');
}

float round_to_bf16(float value) {
    uint32_t bits = 0;
    std::memcpy(&bits, &value, sizeof(bits));
    bits &= 0xffff0000U;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

void dump_f32_matrix(const char * path, const float * values, int64_t n_rows, int64_t n_cols) {
    if (!path || !values || n_rows <= 0 || n_cols <= 0) {
        return;
    }
    FILE * file = std::fopen(path, "wb");
    if (!file) {
        std::fprintf(stderr, "[DEBUG-DUMP][llama] failed to open %s\n", path);
        return;
    }
    // Header: magic, rows, cols. Payload is row-major float32.
    const uint32_t header[3] = { 0x51454d42U, (uint32_t)n_rows, (uint32_t)n_cols };
    std::fwrite(header, sizeof(header), 1, file);
    std::fwrite(values, sizeof(float), (size_t)n_rows * (size_t)n_cols, file);
    std::fclose(file);
    std::fprintf(stderr, "[DEBUG-DUMP][llama] embedding path=%s shape=[%lld,%lld]\n",
        path, (long long)n_rows, (long long)n_cols);
}

std::vector<std::pair<int32_t, float>> topk_logits(const float * logits, int32_t n_vocab, int k) {
    std::vector<int32_t> indices((size_t)n_vocab);
    std::iota(indices.begin(), indices.end(), 0);
    k = std::max(1, std::min(k, n_vocab));
    std::partial_sort(indices.begin(), indices.begin() + k, indices.end(),
        [logits](int32_t lhs, int32_t rhs) { return logits[lhs] > logits[rhs]; });
    std::vector<std::pair<int32_t, float>> result;
    result.reserve((size_t)k);
    for (int i = 0; i < k; ++i) {
        result.emplace_back(indices[(size_t)i], logits[indices[(size_t)i]]);
    }
    return result;
}

std::vector<std::pair<int32_t, float>> debug_first_logits(
        llama_context * ctx, const llama_vocab * vocab) {
    std::vector<std::pair<int32_t, float>> result;
    if (!debug_enabled() || !ctx || !vocab) {
        return result;
    }
    // -1 selects the final output position (the first next-token logits after
    // the complete audio+text prompt has been prefetched).
    const float * logits = llama_get_logits_ith(ctx, -1);
    const int32_t n_vocab = llama_vocab_n_tokens(vocab);
    if (!logits || n_vocab <= 0) {
        std::fprintf(stderr, "[DEBUG-LOGITS][llama] logits unavailable\n");
        return result;
    }
    int k = 10;
    if (const char * value = std::getenv("QWEN3ASR_LOGITS_TOPK")) {
        k = std::atoi(value);
    }
    result = topk_logits(logits, n_vocab, k);
    std::fprintf(stderr, "[DEBUG-LOGITS][llama] first_position_topk=");
    for (size_t i = 0; i < result.size(); ++i) {
        std::fprintf(stderr, "%s{\"id\":%d,\"logit\":%.9g}",
            i == 0 ? "[" : ",", result[i].first, result[i].second);
    }
    std::fprintf(stderr, "]\n");
    return result;
}

void debug_embedding(const float * values, int64_t n_tokens, int64_t n_embd, size_t chunk_index) {
    if (!debug_enabled() || values == nullptr || n_tokens <= 0 || n_embd <= 0) {
        return;
    }
    const size_t count = static_cast<size_t>(n_tokens) * static_cast<size_t>(n_embd);
    double sum = 0.0;
    double sum_sq = 0.0;
    float min_value = values[0];
    float max_value = values[0];
    for (size_t i = 0; i < count; ++i) {
        const float value = values[i];
        sum += value;
        sum_sq += static_cast<double>(value) * value;
        min_value = std::min(min_value, value);
        max_value = std::max(max_value, value);
    }
    std::fprintf(stderr,
        "[DEBUG-EMB][llama] chunk=%zu shape=[%lld,%lld] min=%.9g max=%.9g mean=%.9g rms=%.9g checksum=0x%016llx first8=",
        chunk_index, (long long)n_tokens, (long long)n_embd,
        min_value, max_value, sum / count, std::sqrt(sum_sq / count),
        (unsigned long long)fnv1a_f32(values, count));
    const int64_t n_first = std::min<int64_t>(8, n_embd);
    for (int64_t i = 0; i < n_first; ++i) {
        std::fprintf(stderr, "%.9g%s", values[i], i + 1 == n_first ? "" : ",");
    }
    std::fprintf(stderr, "\n");
}

class Qwen3ASRNative {
public:
    Qwen3ASRNative(
            const std::string & model_path,
            const std::string & mmproj_path,
            int n_ctx = 32768,
            int n_batch = 8192,
            int n_threads = 32,
            bool use_gpu = true,
            int n_gpu_layers = -1) {
        ggml_log_set(private_worker_log, nullptr);
        llama_log_set(private_worker_log, nullptr);
        mtmd_log_set(private_worker_log, nullptr);
        n_batch_ = n_batch;
        llama_backend_init();

        llama_model_params model_params = llama_model_default_params();
        model_params.n_gpu_layers = n_gpu_layers;
        model_.reset(llama_model_load_from_file(model_path.c_str(), model_params));
        if (!model_) {
            throw std::runtime_error("failed to load llama model: " + model_path);
        }

        llama_context_params context_params = llama_context_default_params();
        context_params.n_ctx = static_cast<uint32_t>(n_ctx);
        context_params.n_batch = static_cast<uint32_t>(n_batch);
        context_params.n_ubatch = static_cast<uint32_t>(std::min(n_batch, 512));
        context_params.n_seq_max = 1;
        context_params.n_threads = n_threads;
        context_params.n_threads_batch = n_threads;
        context_params.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        context_params.offload_kqv = use_gpu;
        context_.reset(llama_init_from_model(model_.get(), context_params));
        if (!context_) {
            throw std::runtime_error("failed to create llama context");
        }

        mtmd_context_params mtmd_params = mtmd_context_params_default();
        mtmd_params.use_gpu = use_gpu;
        mtmd_params.n_threads = n_threads;
        mtmd_params.warmup = false;
        mtmd_.reset(mtmd_init_from_file(mmproj_path.c_str(), model_.get(), mtmd_params));
        if (!mtmd_) {
            throw std::runtime_error("failed to load mtmd projector: " + mmproj_path);
        }
        if (!mtmd_support_audio(mtmd_.get())) {
            throw std::runtime_error("the mtmd projector does not support audio");
        }
    }

    // Tokenize text with the vocabulary embedded in the loaded GGUF model.
    // This is used by the streaming prefix-rollback path so that the token
    // boundaries match the tokenizer used by llama.cpp during generation.
    std::vector<llama_token> tokenize(
            const std::string & text,
            bool add_special = false,
            bool parse_special = true) const {
        const llama_vocab * vocab = llama_model_get_vocab(model_.get());
        if (!vocab) {
            throw std::runtime_error("llama model has no vocabulary");
        }
        if (text.size() > static_cast<size_t>(std::numeric_limits<int32_t>::max())) {
            throw std::invalid_argument("text is too large to tokenize");
        }

        // llama_tokenize() returns a negative value when the supplied buffer
        // is too small; the absolute value is the required token count.
        const size_t initial_capacity = std::max<size_t>(2, text.size() + 2);
        std::vector<llama_token> tokens(initial_capacity);
        int32_t count = llama_tokenize(
                vocab,
                text.data(),
                static_cast<int32_t>(text.size()),
                tokens.data(),
                static_cast<int32_t>(tokens.size()),
                add_special,
                parse_special);

        if (count == std::numeric_limits<int32_t>::min()) {
            throw std::runtime_error("llama_tokenize failed: result exceeds int32_t limit");
        }
        if (count < 0) {
            const size_t required = static_cast<size_t>(-count);
            tokens.resize(required);
            count = llama_tokenize(
                    vocab,
                    text.data(),
                    static_cast<int32_t>(text.size()),
                    tokens.data(),
                    static_cast<int32_t>(tokens.size()),
                    add_special,
                    parse_special);
        }
        if (count < 0) {
            throw std::runtime_error("llama_tokenize failed with code " + std::to_string(count));
        }

        tokens.resize(static_cast<size_t>(count));
        return tokens;
    }

    // Detokenize token IDs with the vocabulary embedded in the loaded GGUF.
    // `unparse_special=true` preserves special/control-token text, which is
    // needed when a streaming prefix contains markers such as <asr_text>.
    std::string detokenize_tokens(
            const std::vector<llama_token> & tokens,
            bool unparse_special = true) const {
        if (tokens.empty()) {
            return {};
        }

        const llama_vocab * vocab = llama_model_get_vocab(model_.get());
        if (!vocab) {
            throw std::runtime_error("llama model has no vocabulary");
        }
        if (tokens.size() > static_cast<size_t>(std::numeric_limits<int32_t>::max())) {
            throw std::invalid_argument("too many tokens to detokenize");
        }

        // The API reports the required size as a negative value when this
        // buffer is too small, so retry with the exact size in that case.
        size_t initial_capacity = std::max<size_t>(64, tokens.size() * 8);
        initial_capacity = std::min(
                initial_capacity,
                static_cast<size_t>(std::numeric_limits<int32_t>::max()));
        std::string text(initial_capacity, '\0');
        int32_t count = llama_detokenize(
                vocab,
                tokens.data(),
                static_cast<int32_t>(tokens.size()),
                text.data(),
                static_cast<int32_t>(text.size()),
                false,             // remove_special
                unparse_special);

        if (count < 0) {
            if (count == std::numeric_limits<int32_t>::min()) {
                throw std::runtime_error("llama_detokenize failed: result exceeds int32_t limit");
            }
            text.resize(static_cast<size_t>(-count));
            count = llama_detokenize(
                    vocab,
                    tokens.data(),
                    static_cast<int32_t>(tokens.size()),
                    text.data(),
                    static_cast<int32_t>(text.size()),
                    false,
                    unparse_special);
        }
        if (count < 0) {
            throw std::runtime_error("llama_detokenize failed with code " + std::to_string(count));
        }

        text.resize(static_cast<size_t>(count));
        return text;
    }

    py::dict generate_once(
            py::array_t<float, py::array::c_style | py::array::forcecast> audio,
            const std::string & prompt,
            int max_tokens = 4096) {
        // 音频 encoder，llm 推理
        if (audio.ndim() != 1 || audio.size() == 0) {
            throw std::invalid_argument("audio must be a non-empty 1-D float32 array");
        }
        if (max_tokens <= 0) {
            throw std::invalid_argument("max_tokens must be positive");
        }

        const auto info = audio.request();
        const auto * audio_data = static_cast<const float *>(info.ptr);

        std::string generated_text;
        std::vector<llama_token> generated;
        std::vector<llama_token> prompt_text_tokens;
        std::vector<size_t> media_token_counts;
        std::vector<llama_pos> media_position_counts;
        std::vector<float> all_embeddings;
        std::vector<float> all_decode_embeddings;
        const int64_t embedding_dim = llama_model_n_embd_inp(model_.get());
        const char * embedding_dump_path = std::getenv("QWEN3ASR_EMBED_DUMP_PATH");
        const char * decode_embedding_dump_path = std::getenv("QWEN3ASR_EMBED_DECODE_DUMP_PATH");
        size_t prompt_token_count = 0;
        llama_pos prompt_position_count = 0;
        size_t chunk_count = 0;
        std::string finish_reason;
        std::vector<std::pair<int32_t, float>> first_logits_topk;
        {
            py::gil_scoped_release release;

            llama_memory_clear(llama_get_memory(context_.get()), true);

            // vLLM uses the Qwen3-ASR token triplet. mtmd consumes its own media marker;
            // the marker is expanded into the audio embedding chunk during tokenization.
            std::string mtmd_prompt = replace_all(
                    prompt,
                    "<|audio_start|><|audio_pad|><|audio_end|>",
                    mtmd_default_marker());
            if (mtmd_prompt.find(mtmd_default_marker()) == std::string::npos) {
                throw std::invalid_argument("prompt must contain one Qwen3-ASR audio marker");
            }

            bitmap_ptr bitmap(mtmd_bitmap_init_from_audio(
                    static_cast<size_t>(audio.size()), audio_data));
            if (!bitmap) {
                throw std::runtime_error("failed to create mtmd audio bitmap");
            }

            mtmd_input_text text{
                mtmd_prompt.data(),
                mtmd_prompt.size(),
                true,  // add_special
                true,  // parse_special
            };
            const mtmd_bitmap * bitmaps[] = {bitmap.get()};
            chunks_ptr chunks(mtmd_input_chunks_init());
            const int32_t tokenize_result = mtmd_tokenize(
                    mtmd_.get(), chunks.get(), &text, bitmaps, 1);
            if (tokenize_result != 0) {
                throw std::runtime_error("mtmd_tokenize failed with code " + std::to_string(tokenize_result));
            }

            llama_pos n_past = 0;
            const size_t n_chunks = mtmd_input_chunks_size(chunks.get());
            chunk_count = n_chunks;
            size_t media_chunk_index = 0;
            prompt_token_count = mtmd_helper_get_n_tokens(chunks.get());
            prompt_position_count = mtmd_helper_get_n_pos(chunks.get());
            for (size_t i = 0; i < n_chunks; ++i) {
                const mtmd_input_chunk * chunk = mtmd_input_chunks_get(chunks.get(), i);
                const bool is_last = i + 1 == n_chunks;
                llama_pos new_n_past = n_past;
                int32_t result = 0;

                if (mtmd_input_chunk_get_type(chunk) == MTMD_INPUT_CHUNK_TYPE_TEXT) {
                    size_t n_text_tokens = 0;
                    const llama_token * text_tokens = mtmd_input_chunk_get_tokens_text(
                            chunk, &n_text_tokens);
                    if (text_tokens && n_text_tokens > 0) {
                        prompt_text_tokens.insert(
                                prompt_text_tokens.end(), text_tokens, text_tokens + n_text_tokens);
                    }
                    result = mtmd_helper_eval_chunk_single(
                        mtmd_.get(), context_.get(), chunk, n_past, 0, n_batch_,
                            is_last, &new_n_past);
                } else {
                    // Audio and image chunks use different internal token
                    // structures. The generic chunk accessors work for both
                    // and correctly report the media span.
                    media_token_counts.push_back(mtmd_input_chunk_get_n_tokens(chunk));
                    media_position_counts.push_back(mtmd_input_chunk_get_n_pos(chunk));
                    result = mtmd_encode_chunk(mtmd_.get(), chunk);
                    if (result == 0) {
                        float * embedding = mtmd_get_output_embd(mtmd_.get());
                        if (!embedding) {
                            throw std::runtime_error("mtmd returned no audio embedding");
                        }
                        debug_embedding(
                                embedding,
                                mtmd_input_chunk_get_n_tokens(chunk),
                                llama_model_n_embd_inp(model_.get()),
                                media_chunk_index++);
                        if (embedding_dump_path && embedding_dim > 0) {
                            const size_t n_values = static_cast<size_t>(mtmd_input_chunk_get_n_tokens(chunk))
                                * static_cast<size_t>(embedding_dim);
                            all_embeddings.insert(all_embeddings.end(), embedding, embedding + n_values);
                        }
                        std::vector<float> embedding_bf16;
                        float * embedding_for_decode = embedding;
                        if (embed_bf16_enabled()) {
                            const size_t n_values = static_cast<size_t>(mtmd_input_chunk_get_n_tokens(chunk))
                                * static_cast<size_t>(llama_model_n_embd_inp(model_.get()));
                            embedding_bf16.resize(n_values);
                            for (size_t j = 0; j < n_values; ++j) {
                                embedding_bf16[j] = round_to_bf16(embedding[j]);
                            }
                            embedding_for_decode = embedding_bf16.data();
                        }
                        if (decode_embedding_dump_path && embedding_dim > 0) {
                            const size_t n_values = static_cast<size_t>(mtmd_input_chunk_get_n_tokens(chunk))
                                * static_cast<size_t>(embedding_dim);
                            all_decode_embeddings.insert(
                                all_decode_embeddings.end(), embedding_for_decode, embedding_for_decode + n_values);
                        }
                        result = mtmd_helper_decode_image_chunk(
                                mtmd_.get(), context_.get(), chunk, embedding_for_decode,
                                n_past, 0, n_batch_, &new_n_past, nullptr, nullptr);
                    }
                }
                if (result != 0) {
                    throw std::runtime_error("failed to evaluate mtmd chunk " + std::to_string(i) +
                                             ", code " + std::to_string(result));
                }
                n_past = new_n_past;
            }

            if (embedding_dump_path && embedding_dim > 0 && !all_embeddings.empty()) {
                dump_f32_matrix(
                    embedding_dump_path,
                    all_embeddings.data(),
                    (int64_t)(all_embeddings.size() / (size_t)embedding_dim),
                    embedding_dim);
            }
            if (decode_embedding_dump_path && embedding_dim > 0 && !all_decode_embeddings.empty()) {
                dump_f32_matrix(
                    decode_embedding_dump_path,
                    all_decode_embeddings.data(),
                    (int64_t)(all_decode_embeddings.size() / (size_t)embedding_dim),
                    embedding_dim);
            }

            sampler_ptr sampler(llama_sampler_init_greedy());
            if (!sampler) {
                throw std::runtime_error("failed to create greedy sampler");
            }

            const llama_vocab * vocab = llama_model_get_vocab(model_.get());
            first_logits_topk = debug_first_logits(context_.get(), vocab);
            generated.reserve(static_cast<size_t>(max_tokens));
            finish_reason = "length";

            for (int i = 0; i < max_tokens; ++i) {
                const llama_token token = llama_sampler_sample(sampler.get(), context_.get(), -1);
                generated.push_back(token);
                llama_sampler_accept(sampler.get(), token);

                if (llama_vocab_is_eog(vocab, token)) {
                    finish_reason = "stop";
                    break;
                }

                llama_token next_token = token;
                llama_batch batch = llama_batch_get_one(&next_token, 1);
                if (llama_decode(context_.get(), batch) != 0) {
                    throw std::runtime_error("llama_decode failed during generation");
                }
                ++n_past;
            }
            // Decode the full token sequence: individual pieces can split a
            // UTF-8 character and llama_token_to_piece would corrupt it.
            const size_t text_count = finish_reason == "stop" ? generated.size() - 1 : generated.size();
            generated_text = detokenize_tokens(
                    std::vector<llama_token>(generated.begin(), generated.begin() + text_count), true);
        }

        py::dict result;
        result["text"] = generated_text;
        result["token_ids"] = generated;
        result["finish_reason"] = finish_reason;
        result["prompt_text_token_ids"] = prompt_text_tokens;
        result["prompt_token_count"] = prompt_token_count;
        result["prompt_position_count"] = prompt_position_count;
        result["media_token_counts"] = media_token_counts;
        result["media_position_counts"] = media_position_counts;
        result["chunk_count"] = chunk_count;
        py::list logits_topk;
        for (const auto & item : first_logits_topk) {
            py::dict entry;
            entry["id"] = item.first;
            entry["logit"] = item.second;
            logits_topk.append(entry);
        }
        result["first_logits_topk"] = logits_topk;
        return result;
    }

    // Run exactly the same prefill path as generate_once_with_embedding(), but
    // return the logits at the final prompt position instead of generating.
    py::array_t<float> prefill_logits_with_embedding(
            py::array_t<float, py::array::c_style | py::array::forcecast> external_embd,
            const std::string & prompt) {
        std::vector<float> logits_copy;
        int32_t n_vocab = 0;
        {
            py::gil_scoped_release release;
            prefill_only_with_embedding(external_embd, prompt);
            const llama_vocab * vocab = llama_model_get_vocab(model_.get());
            n_vocab = llama_vocab_n_tokens(vocab);
            const float * logits = llama_get_logits_ith(context_.get(), -1);
            if (!logits) {
                throw std::runtime_error("llama_get_logits_ith returned null");
            }
            logits_copy.assign(logits, logits + n_vocab);
        }
        // Windows/MSVC has no global POSIX ssize_t; pybind11 provides the
        // platform-correct signed array-size type.
        py::array_t<float> array(static_cast<py::ssize_t>(n_vocab));
        std::memcpy(array.mutable_data(), logits_copy.data(), logits_copy.size() * sizeof(float));
        return array;
    }

    // Skip mtmd's local audio encoder and feed an externally-produced audio
    // embedding directly into the llama decoder.
    py::dict generate_once_with_embedding(
            py::array_t<float, py::array::c_style | py::array::forcecast> external_embd,
            const std::string & prompt,
            int max_tokens = 4096) {
        if (max_tokens <= 0) {
            throw std::invalid_argument("max_tokens must be positive");
        }

        std::string generated_text;
        std::vector<llama_token> generated;
        std::string finish_reason;
        {
            py::gil_scoped_release release;

            llama_pos n_past = prefill_only_with_embedding(external_embd, prompt);
            const llama_vocab * vocab = llama_model_get_vocab(model_.get());
            const int32_t n_vocab = llama_vocab_n_tokens(vocab);
            generated.reserve(static_cast<size_t>(max_tokens));
            finish_reason = "length";

            for (int i = 0; i < max_tokens; ++i) {
                const float * logits = llama_get_logits_ith(context_.get(), -1);
                if (!logits) {
                    throw std::runtime_error("llama_get_logits_ith returned null");
                }
                const llama_token token = pick_token_greedy(logits, n_vocab);
                generated.push_back(token);
                if (llama_vocab_is_eog(vocab, token)) {
                    finish_reason = "stop";
                    break;
                }

                llama_token next_token = token;
                llama_batch batch = llama_batch_get_one(&next_token, 1);
                if (llama_decode(context_.get(), batch) != 0) {
                    throw std::runtime_error("llama_decode failed during generation");
                }
                ++n_past;
            }
            const size_t text_count = finish_reason == "stop" ? generated.size() - 1 : generated.size();
            generated_text = detokenize_tokens(
                    std::vector<llama_token>(generated.begin(), generated.begin() + text_count), true);
        }

        py::dict result;
        result["text"] = generated_text;
        result["token_ids"] = generated;
        result["finish_reason"] = finish_reason;
        return result;
    }

private:
    // Shared external-embedding prefill path:
    // clear KV -> pre-marker text -> external embedding -> post-marker text.
    llama_pos prefill_only_with_embedding(
            const py::array_t<float, py::array::c_style | py::array::forcecast> & external_embd,
            const std::string & prompt) {
        if (external_embd.ndim() != 2 || external_embd.shape(0) == 0) {
            throw std::invalid_argument(
                    "external_embd must be a non-empty 2-D [n_tokens, n_embd] float32 array");
        }
        const int32_t n_embd_inp = llama_model_n_embd_inp(model_.get());
        if (static_cast<int32_t>(external_embd.shape(1)) != n_embd_inp) {
            throw std::invalid_argument(
                    "external_embd second dim (" + std::to_string(external_embd.shape(1)) +
                    ") must equal llama_model_n_embd_inp (" + std::to_string(n_embd_inp) + ")");
        }

        const std::string marker = "<|audio_pad|>";
        const size_t marker_pos = prompt.find(marker);
        if (marker_pos == std::string::npos) {
            throw std::invalid_argument("prompt must contain exactly one <|audio_pad|> placeholder");
        }
        if (prompt.find(marker, marker_pos + marker.size()) != std::string::npos) {
            throw std::invalid_argument("prompt must contain exactly one <|audio_pad|> placeholder");
        }
        const std::string pre_text = prompt.substr(0, marker_pos);
        const std::string post_text = prompt.substr(marker_pos + marker.size());

        const auto embd_info = external_embd.request();
        std::vector<float> embd_buf(
                static_cast<const float *>(embd_info.ptr),
                static_cast<const float *>(embd_info.ptr) + external_embd.size());
        const int32_t n_embd_tokens = static_cast<int32_t>(external_embd.shape(0));

        llama_memory_clear(llama_get_memory(context_.get()), true);
        const llama_vocab * vocab = llama_model_get_vocab(model_.get());

        auto tokenize_text = [&](const std::string & text, bool add_special) {
            std::vector<llama_token> tokens(text.size() + 16);
            int32_t count = llama_tokenize(
                    vocab,
                    text.c_str(),
                    static_cast<int32_t>(text.size()),
                    tokens.data(),
                    static_cast<int32_t>(tokens.size()),
                    add_special,
                    true);
            if (count < 0) {
                tokens.resize(static_cast<size_t>(-count));
                count = llama_tokenize(
                        vocab,
                        text.c_str(),
                        static_cast<int32_t>(text.size()),
                        tokens.data(),
                        static_cast<int32_t>(tokens.size()),
                        add_special,
                        true);
            }
            tokens.resize(static_cast<size_t>(std::max(count, 0)));
            return tokens;
        };

        llama_pos n_past = 0;

        std::vector<llama_token> pre_tokens = tokenize_text(pre_text, true);
        if (!pre_tokens.empty()) {
            llama_batch batch = llama_batch_get_one(
                    pre_tokens.data(), static_cast<int32_t>(pre_tokens.size()));
            if (llama_decode(context_.get(), batch) != 0) {
                throw std::runtime_error("llama_decode failed on pre-text");
            }
            n_past += static_cast<llama_pos>(pre_tokens.size());
        }

        const int n_pos_per_embd = mtmd_decode_use_mrope(mtmd_.get()) ? 4 : 1;
        const bool use_non_causal = mtmd_decode_use_non_causal(mtmd_.get(), nullptr);
        {
            decode_embd_batch batch_embd(
                    embd_buf.data(), n_embd_tokens, n_pos_per_embd, n_embd_inp);
            if (n_pos_per_embd == 4) {
                batch_embd.set_position_mrope_1d(n_past, 0);
            } else {
                batch_embd.set_position_normal(n_past, 0);
            }
            llama_set_causal_attn(context_.get(), !use_non_causal);
            int32_t batch_index = 0;
            const int32_t n_embedding_batches = (n_embd_tokens + n_batch_ - 1) / n_batch_;
            while (batch_index < n_embedding_batches) {
                const int position_offset = batch_index * n_batch_;
                const int n_tokens_batch = std::min(n_batch_, n_embd_tokens - position_offset);
                llama_batch view = batch_embd.get_view(position_offset, n_tokens_batch);
                if (llama_decode(context_.get(), view) != 0) {
                    llama_set_causal_attn(context_.get(), true);
                    throw std::runtime_error("llama_decode failed on external embedding batch");
                }
                ++batch_index;
            }
            llama_set_causal_attn(context_.get(), true);
            n_past += static_cast<llama_pos>(n_embd_tokens);
        }

        std::vector<llama_token> post_tokens = tokenize_text(post_text, false);
        if (!post_tokens.empty()) {
            llama_batch batch = llama_batch_get_one(
                    post_tokens.data(), static_cast<int32_t>(post_tokens.size()));
            if (llama_decode(context_.get(), batch) != 0) {
                throw std::runtime_error("llama_decode failed on post-text");
            }
            n_past += static_cast<llama_pos>(post_tokens.size());
        }
        return n_past;
    }

    model_ptr model_;
    context_ptr context_;
    mtmd_ptr mtmd_;
    int n_batch_ = 8192;
};

} // namespace

PYBIND11_MODULE(qwen3asr_native, m) {
    m.doc() = "In-process Qwen3-ASR backend backed by libllama and libmtmd";
    py::class_<Qwen3ASRNative>(m, "Qwen3ASRNative")
        .def(py::init<const std::string &, const std::string &, int, int, int, bool, int>(),
             py::arg("model_path"),
             py::arg("mmproj_path"),
             py::arg("n_ctx") = 32768,
             py::arg("n_batch") = 8192,
             py::arg("n_threads") = 32,
             py::arg("use_gpu") = true,
             py::arg("n_gpu_layers") = -1)
        .def("tokenize", &Qwen3ASRNative::tokenize,
             py::arg("text"),
             py::arg("add_special") = false,
             py::arg("parse_special") = true)
        .def("detokenize", &Qwen3ASRNative::detokenize_tokens,
             py::arg("tokens"),
             py::arg("special") = true)
        .def("generate_once", &Qwen3ASRNative::generate_once,
             py::arg("audio"), py::arg("prompt"), py::arg("max_tokens") = 4096)
        .def("generate_once_with_embedding", &Qwen3ASRNative::generate_once_with_embedding,
             py::arg("external_embd"), py::arg("prompt"), py::arg("max_tokens") = 4096)
        .def("prefill_logits_with_embedding", &Qwen3ASRNative::prefill_logits_with_embedding,
             py::arg("external_embd"), py::arg("prompt"));
}
