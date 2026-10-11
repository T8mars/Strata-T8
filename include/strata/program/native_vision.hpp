// Opt-in GENIV contract. No CUDA, tokenizer or model dependency.
#pragma once
#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <limits>
#include <string>
#include <vector>

namespace strata::program::native_vision {
constexpr int64_t image_pad = 248056, video_pad = 248057;
constexpr size_t max_spans = 256, max_manifest_bytes = 32768;
struct Span { int64_t start, pad, rows; };
struct Record { int64_t rows, nx, ny, start, pad, pos; size_t off; };
struct Batch { std::vector<float> rows; std::vector<int32_t> positions; std::vector<Record> records; };

inline bool decimal(const char* first, const char* last, int64_t& value) {
    if (first == last) return false;
    const auto r = std::from_chars(first, last, value);
    return r.ec == std::errc{} && r.ptr == last && value >= 0;
}
inline bool parse_spans(const std::string& text, std::vector<Span>& out, std::string& err) {
    out.clear();
    if (text.empty() || text.size() > max_manifest_bytes) { err = "empty or oversized media spans"; return false; }
    size_t at = 0;
    while (at < text.size()) {
        const size_t end = text.find(',', at), stop = end == std::string::npos ? text.size() : end;
        const size_t a = text.find(':', at), b = a == std::string::npos ? a : text.find(':', a + 1);
        Span s{};
        if (out.size() >= max_spans || a >= stop || b >= stop ||
            !decimal(text.data() + at, text.data() + a, s.start) ||
            !decimal(text.data() + a + 1, text.data() + b, s.pad) ||
            !decimal(text.data() + b + 1, text.data() + stop, s.rows) || s.rows < 1 ||
            (s.pad != image_pad && s.pad != video_pad)) {
            err = "bad media span (expected start:pad_id:rows, at most 256)"; return false;
        }
        out.push_back(s);
        if (end == std::string::npos) break;
        at = end + 1;
        if (at == text.size()) { err = "trailing media span separator"; return false; }
    }
    return true;
}
inline bool parse_ids(const char* text, int64_t limit, int64_t vocab,
                      std::vector<int64_t>& ids, std::string& err) {
    ids.clear();
    if (!text || limit < 1) { err = "empty native prompt"; return false; }
    while (*text == ' ') ++text;
    const char* last = text + std::char_traits<char>::length(text);
    while (*text) {
        if (ids.size() >= (uint64_t) limit) { err = "native prompt exceeds context"; return false; }
        int64_t id = -1;
        const auto r = std::from_chars(text, last, id);
        if (r.ec != std::errc{} || id < 0 || id >= vocab || (*r.ptr && *r.ptr != ',')) {
            err = "bad native token id"; return false;
        }
        ids.push_back(id);
        if (!*r.ptr) break;
        text = r.ptr + 1;
        if (!*text) { err = "trailing native token separator"; return false; }
    }
    if (ids.empty()) { err = "empty native prompt"; return false; }
    return true;
}

// All context, span, grid, byte and position checks precede payload allocation.
// The caller publishes the returned table only after this entire transaction succeeds.
inline bool read(const std::string& path, const std::vector<Span>& spans,
                 const std::vector<int64_t>& ids, int64_t width, int64_t cells,
                 int64_t context, int64_t max_new, Batch& out, std::string& err) {
    out = {};
    const int64_t n = (int64_t) ids.size();
    if (width < 1 || width > 1048576 || cells < 1 || cells > INT32_MAX || context < 9 ||
        max_new < 1 || max_new > context - 8 || n < 1 || n > context - 8 - max_new || n > cells ||
        spans.empty() || spans.size() > max_spans) { err = "native prompt/context/cell bounds"; return false; }
    int64_t covered_end = 0, total_rows = 0;
    for (const Span& s : spans) {
        if (s.start < covered_end || s.start < 0 || s.start >= n || s.rows < 1 || s.rows > n - s.start ||
            (s.pad != image_pad && s.pad != video_pad)) { err = "unordered or out-of-range media span"; return false; }
        for (int64_t i = covered_end; i < s.start; ++i)
            if (ids[(size_t) i] == image_pad || ids[(size_t) i] == video_pad) { err = "uncovered media pad"; return false; }
        for (int64_t i = s.start; i < s.start + s.rows; ++i)
            if (ids[(size_t) i] != s.pad) { err = "media span token mismatch"; return false; }
        covered_end = s.start + s.rows;
        total_rows += s.rows; // disjoint spans inside n make this <= n
    }
    for (int64_t i = covered_end; i < n; ++i)
        if (ids[(size_t) i] == image_pad || ids[(size_t) i] == video_pad) { err = "uncovered media pad"; return false; }
    if (covered_end == n) { err = "native prompt cannot end in media"; return false; }
    const uint64_t floats = (uint64_t) total_rows * (uint64_t) width;
    if (floats > SIZE_MAX / sizeof(float) || (uint64_t) cells > SIZE_MAX / (3 * sizeof(int32_t))) {
        err = "native payload size overflow"; return false;
    }
    const uint64_t expected_bytes = floats * sizeof(float) + spans.size() * 20;
    if (expected_bytes > (uint64_t) std::numeric_limits<std::streamoff>::max()) { err = "native file size overflow"; return false; }
    std::ifstream f(std::filesystem::u8path(path), std::ios::binary | std::ios::ate);
    if (!f || f.tellg() != (std::streamoff) expected_bytes) { err = "native embeddings file length mismatch"; return false; }
    f.seekg(0);
    Batch next;
    size_t off = 0;
    int64_t p = 0, previous = 0;
    for (const Span& s : spans) {
        int32_t h[5]{};
        if (!f.read(reinterpret_cast<char*>(h), sizeof h) || h[0] != 0x31455653 ||
            h[1] != s.rows || h[2] < 1 || h[3] < 1 || (int64_t) h[2] * h[3] != h[1] || h[4] != width) {
            err = "bad native SVE1 record/grid/width"; return false;
        }
        p += s.start - previous;
        if (p > INT32_MAX - std::max(h[2], h[3])) { err = "native rotary position overflow"; return false; }
        next.records.push_back({s.rows, h[2], h[3], s.start, s.pad, p, off});
        const uint64_t count = (uint64_t) s.rows * (uint64_t) width;
        f.seekg((std::streamoff) (count * sizeof(float)), std::ios::cur);
        if (!f) { err = "short native SVE1 payload"; return false; }
        off += (size_t) count;
        p += std::max(h[2], h[3]);
        previous = s.start + s.rows;
    }
    p += n - previous;
    if (p > INT32_MAX - (cells - n)) { err = "native decode position overflow"; return false; }
    next.rows.resize((size_t) floats);
    f.seekg(0);
    for (const Record& r : next.records) {
        f.seekg(20, std::ios::cur);
        const size_t count = (size_t) r.rows * (size_t) width;
        if (!f.read(reinterpret_cast<char*>(next.rows.data() + r.off), (std::streamsize) (count * sizeof(float)))) {
            err = "short native embeddings read"; return false;
        }
        for (size_t j = 0; j < count; ++j)
            if (!std::isfinite(next.rows[r.off + j])) { err = "non-finite native embedding"; return false; }
    }
    next.positions.resize((size_t) cells * 3);
    auto put = [&](int64_t i, int64_t t, int64_t h, int64_t w) {
        next.positions[(size_t) i * 3] = (int32_t) t;
        next.positions[(size_t) i * 3 + 1] = (int32_t) h;
        next.positions[(size_t) i * 3 + 2] = (int32_t) w;
    };
    int64_t i = 0; p = 0;
    for (const Record& r : next.records) {
        while (i < r.start) { put(i++, p, p, p); ++p; }
        for (int64_t j = 0; j < r.rows; ++j) put(i + j, p, p + j / r.nx, p + j % r.nx);
        i += r.rows; p += std::max(r.nx, r.ny);
    }
    while (i < cells) { put(i++, p, p, p); ++p; }
    out = std::move(next);
    return true;
}
} // namespace strata::program::native_vision
