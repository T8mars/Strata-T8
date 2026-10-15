// Strata-T8 portable entry. Uses the bundled interpreter; no shell or system Python.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <string>
#include <vector>

static std::wstring quote(const std::wstring &value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') { ++slashes; continue; }
        if (ch == L'\"') result.append(slashes * 2 + 1, L'\\');
        else result.append(slashes, L'\\');
        result.push_back(ch);
        slashes = 0;
    }
    result.append(slashes * 2, L'\\');
    return result + L"\"";
}

static void write_error(const std::wstring &message) {
    const std::wstring text = L"[Strata-T8] " + message + L"\r\n";
    HANDLE output = GetStdHandle(STD_ERROR_HANDLE);
    DWORD mode = 0, written = 0;
    if (GetConsoleMode(output, &mode)) {
        WriteConsoleW(output, text.data(), static_cast<DWORD>(text.size()), &written, nullptr);
    } else {
        int count = WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
        std::string bytes(count, '\0');
        WideCharToMultiByte(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), bytes.data(), count, nullptr, nullptr);
        WriteFile(output, bytes.data(), static_cast<DWORD>(bytes.size()), &written, nullptr);
    }
}

static int finish(DWORD code, bool interactive) {
    DWORD mode = 0;
    if (code && interactive && GetConsoleMode(GetStdHandle(STD_INPUT_HANDLE), &mode)) {
        write_error(L"Startup did not finish. See logs, or run CHECK-ENV.bat. Press Enter to close.");
        wchar_t buffer[4]; DWORD read = 0;
        ReadConsoleW(GetStdHandle(STD_INPUT_HANDLE), buffer, 3, &read, nullptr);
    }
    return static_cast<int>(code);
}

int wmain(int argc, wchar_t **argv) {
    SetConsoleOutputCP(CP_UTF8);
    SetConsoleCP(CP_UTF8);
    SetConsoleTitleW(L"Strata-T8 | By T8star");
    std::vector<wchar_t> path(32768);
    DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!length || length >= path.size()) {
        write_error(L"Cannot locate the portable installation.");
        return finish(2, argc == 1);
    }
    std::wstring root(path.data(), length);
    root.resize(root.find_last_of(L"\\/"));
    const std::wstring python = root + L"\\runtime\\python\\python.exe";
    const std::wstring script = root + L"\\portable.py";
    for (const auto &required : {python, script}) {
        DWORD attributes = GetFileAttributesW(required.c_str());
        if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
            write_error(L"Missing bundled file: " + required + L". Extract the complete package; keep runtime beside this EXE.");
            return finish(2, argc == 1);
        }
    }
    SetEnvironmentVariableW(L"PYTHONHOME", nullptr);
    SetEnvironmentVariableW(L"PYTHONPATH", nullptr);
    SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1");
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", L"1");
    std::vector<std::wstring> args{python, L"-X", L"utf8", L"-u", script};
    int first = 1;
    std::wstring action = argc > 1 ? argv[1] : L"";
    if (action == L"--check") { args.push_back(L"check"); first = 2; }
    else if (action == L"--settings") { args.push_back(L"settings"); first = 2; }
    else if (action != L"start" && action != L"settings" && action != L"check" && action != L"import" && action != L"configure") {
        args.push_back(L"start");
    }
    for (int index = first; index < argc; ++index) args.emplace_back(argv[index]);
    std::wstring command;
    for (const auto &arg : args) { if (!command.empty()) command += L" "; command += quote(arg); }
    STARTUPINFOW startup{}; startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    // No kill-on-close Job Object: the confirmed update helper must survive this proxy.
    if (!CreateProcessW(python.c_str(), command.data(), nullptr, nullptr, TRUE, 0, nullptr,
                        root.c_str(), &startup, &process)) {
        write_error(L"Cannot start bundled Python (Windows error " + std::to_wstring(GetLastError()) + L").");
        return finish(2, argc == 1);
    }
    CloseHandle(process.hThread);
    DWORD waited = WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 2;
    if (waited == WAIT_OBJECT_0) GetExitCodeProcess(process.hProcess, &code);
    else write_error(L"Could not wait for the launcher process.");
    CloseHandle(process.hProcess);
    return finish(code, argc == 1);
}
