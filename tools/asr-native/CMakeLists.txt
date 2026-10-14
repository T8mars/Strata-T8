cmake_minimum_required(VERSION 3.26)
project(confucius4_r2t2_native LANGUAGES CXX)

set(LLAMA_CPP_DIR "" CACHE PATH "Pinned llama.cpp source checkout")
if(NOT EXISTS "${LLAMA_CPP_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Pass -DLLAMA_CPP_DIR=<pinned llama.cpp source>")
endif()

set(BUILD_SHARED_LIBS ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_MTMD ON CACHE BOOL "" FORCE)
option(GGML_CUDA "Build the optional CUDA ASR backend" OFF)

add_subdirectory("${LLAMA_CPP_DIR}" llama_cpp EXCLUDE_FROM_ALL)

find_package(Python COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)

pybind11_add_module(qwen3asr_native native_ext.cpp)
target_compile_features(qwen3asr_native PRIVATE cxx_std_17)
target_include_directories(qwen3asr_native PRIVATE
    "${LLAMA_CPP_DIR}/include"
    "${LLAMA_CPP_DIR}/tools/mtmd"
)
target_link_libraries(qwen3asr_native PRIVATE mtmd llama)

# Visual Studio uses RUNTIME_OUTPUT_DIRECTORY for .pyd and adds a configuration
# subdirectory unless the configuration-specific property is also defined.
set_target_properties(qwen3asr_native PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/python"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/python"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/python"
)
