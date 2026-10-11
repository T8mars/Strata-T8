#include "strata/program/native_vision.hpp"
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <sstream>
using namespace strata::program::native_vision;
static int checks = 0;
static void require(bool ok, const char* what) {
    ++checks; if (!ok) { std::cerr << "FAIL " << what << '\n'; std::exit(1); }
}
int main(int argc, char** argv) {
    if (argc == 8 && std::string(argv[1]) == "--layout") {
        std::vector<Span> spans; std::vector<int64_t> ids; Batch batch; std::string err;
        const int64_t width = std::stoll(argv[5]), context = std::stoll(argv[6]), maxnew = std::stoll(argv[7]);
        if (!parse_spans(argv[3], spans, err) || !parse_ids(argv[4], context, 248320, ids, err) ||
            !read(argv[2], spans, ids, width, context + 64, context, maxnew, batch, err)) {
            std::cerr << err << '\n'; return 1;
        }
        std::cout << '[';
        for (size_t i = 0; i < batch.positions.size(); ++i) { if (i) std::cout << ','; std::cout << batch.positions[i]; }
        std::cout << "]\n"; return 0;
    }
    const auto path = std::filesystem::u8path(argc > 1 ? argv[1] : "native-contract.sve");
    const auto str = path.u8string();
    std::vector<Span> spans; std::string err;
    require(parse_spans("1:248057:4,6:248056:2", spans, err), "mixed span parse");
    for (const char* bad : {"", "1:248055:4", "1:248057:0", "-1:248057:4", "1:248057:4,", "1:248057:4:5",
                           "9223372036854775808:248057:4", "1:248057", "1:248057:4,,6:248056:2"}) {
        std::vector<Span> x; require(!parse_spans(bad, x, err), "bad span rejected");
    }
    std::string huge;
    for (int i = 0; i < 257; ++i) { if (i) huge += ','; huge += std::to_string(i) + ":248057:1"; }
    std::vector<Span> x; require(!parse_spans(huge, x, err), "257 records rejected");
    std::vector<int64_t> ids;
    require(parse_ids(" 9,248057,248057,248057,248057,10,248056,248056,11", 9, 248060, ids, err), "bounded ids");
    for (const char* bad : {"", "1,", "-1", "248060", "9223372036854775808", "1;2", "1,2,3"}) {
        std::vector<int64_t> y; require(!parse_ids(bad, 2, 248060, y, err), "bad id rejected");
    }
    auto write = [&](int nx = 2, int width = 3, bool nan = false, int tail = 0) {
        std::ofstream f(path, std::ios::binary | std::ios::trunc);
        const int32_t h1[5] = {0x31455653, 4, nx, 2, width}, h2[5] = {0x31455653, 2, 2, 1, 3};
        std::vector<float> a(12, 0.125f), b(6, 0.75f);
        if (nan) a[3] = std::numeric_limits<float>::quiet_NaN();
        f.write(reinterpret_cast<const char*>(h1), 20); f.write(reinterpret_cast<const char*>(a.data()), 48);
        f.write(reinterpret_cast<const char*>(h2), 20); f.write(reinterpret_cast<const char*>(b.data()), 24);
        for (int i = 0; i < tail; ++i) f.put('x');
    };
    write(); Batch batch;
    require(read(str, spans, ids, 3, 32, 32, 2, batch, err), "mixed native records");
    require(batch.rows.size() == 18 && batch.records.size() == 2, "record sizes");
    // Qwen4Exp: each fused block has fixed t; text after it uses max(grid), not pad count.
    const int32_t expected[] = {0,0,0, 1,1,1, 1,1,2, 1,2,1, 1,2,2, 3,3,3, 4,4,4, 4,4,5, 6,6,6, 7,7,7};
    require(std::equal(std::begin(expected), std::end(expected), batch.positions.begin()), "ordered t/h/w golden fixture");
    require(batch.records[0].pad == video_pad && batch.records[1].pad == image_pad && ids[1] == video_pad,
            "real video token preserved");
    auto rejected = [&](const std::vector<Span>& s, const std::vector<int64_t>& t, int64_t cells = 32,
                        int64_t context = 32, int64_t maxnew = 2) {
        Batch z; z.rows = {99};
        const bool ok = read(str, s, t, 3, cells, context, maxnew, z, err);
        require(!ok && z.rows.empty() && z.positions.empty(), "invalid request never publishes rows/table");
    };
    rejected(spans, ids, 8); rejected(spans, ids, 32, 18, 2); rejected(spans, ids, 32, 32, INT64_MAX);
    rejected({spans[1], spans[0]}, ids); rejected({spans[0]}, ids);
    auto wrong = ids; wrong[2] = 12; rejected(spans, wrong);
    auto overlap = spans; overlap[1].start = 4; rejected(overlap, ids);
    auto oversized = spans; oversized[0].rows = INT64_MAX; rejected(oversized, ids);
    auto ended = ids; ended.pop_back(); rejected(spans, ended);
    write(3); rejected(spans, ids); write(2, 4); rejected(spans, ids);
    write(2, 3, true); rejected(spans, ids); write(2, 3, false, 1); rejected(spans, ids);
    std::filesystem::resize_file(path, 111); rejected(spans, ids);
    std::filesystem::remove(path);
    std::cout << "PASS native_vision_contract checks=" << checks << " (CPU only)\n";
    return 0;
}
