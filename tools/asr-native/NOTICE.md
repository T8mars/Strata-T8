# Native backend provenance

`native_ext.cpp` is adapted from
[`netease-youdao/Confucius4-R2T2/r2t2_llama/native_ext.cpp`](https://github.com/netease-youdao/Confucius4-R2T2/blob/26d55a54ce5670cff9947a167d8ed95d569fd4d9/r2t2_llama/native_ext.cpp)
at commit `26d55a54ce5670cff9947a167d8ed95d569fd4d9`. The original project
licenses its code under Apache License 2.0; the corresponding license text is
included in this directory.

This copy adds Windows-compatible pybind sizing, whole-sequence UTF-8
detokenization, visible EOG filtering, and log filtering to avoid writing
prompts and transcripts to the worker log. `CMakeLists.txt` integrates the
adapted file with the pinned llama.cpp checkout.

The build script obtains llama.cpp at commit
`ad6c66839af3c5646fba8c6c2e2087a1e4e38948`. llama.cpp is MIT-licensed;
its source is not committed to this repository. Anyone distributing compiled
native binaries must also comply with that project's license terms.

The GGUF model and projector weights are downloaded separately into the
Git-ignored `models/` directory. They are governed by the upstream
[NetEase Youdao Model Use License Agreement](https://github.com/netease-youdao/Confucius4-R2T2/blob/26d55a54ce5670cff9947a167d8ed95d569fd4d9/MODEL_LICENSE),
not by this directory's Apache license.
