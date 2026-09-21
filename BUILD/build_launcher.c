/* Native Windows launcher for build.py. No CRT or third-party runtime is used. */
#include <windows.h>
#include <shellapi.h>

static BOOL append_character(wchar_t **cursor, SIZE_T *remaining, wchar_t value) {
    if (*remaining <= 1) return FALSE;
    *(*cursor)++ = value;
    --*remaining;
    return TRUE;
}

/* Quote one argv element using the inverse of CommandLineToArgvW's rules. */
static BOOL append_argument(wchar_t **cursor, SIZE_T *remaining, const wchar_t *argument) {
    SIZE_T backslashes = 0;

    if (!append_character(cursor, remaining, L'"')) return FALSE;
    while (*argument) {
        if (*argument == L'\\') {
            ++backslashes;
            ++argument;
            continue;
        }
        if (*argument == L'"') {
            /* Backslashes before a quote are doubled, then the quote is escaped. */
            while (backslashes > 0) {
                --backslashes;
                if (!append_character(cursor, remaining, L'\\') ||
                    !append_character(cursor, remaining, L'\\')) return FALSE;
            }
            if (!append_character(cursor, remaining, L'\\') ||
                !append_character(cursor, remaining, L'"')) return FALSE;
        } else {
            while (backslashes > 0) {
                --backslashes;
                if (!append_character(cursor, remaining, L'\\')) return FALSE;
            }
            if (!append_character(cursor, remaining, *argument)) return FALSE;
        }
        backslashes = 0;
        ++argument;
    }
    /* Backslashes immediately before the closing quote must also be doubled. */
    while (backslashes > 0) {
        --backslashes;
        if (!append_character(cursor, remaining, L'\\') ||
            !append_character(cursor, remaining, L'\\')) return FALSE;
    }
    return append_character(cursor, remaining, L'"');
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR ignored, int show) {
    wchar_t executable[32768], script[32768], *slash, *command, *cursor;
    wchar_t **arguments;
    int argument_count, index;
    /* CreateProcessW accepts at most 32,767 characters including the NUL. */
    SIZE_T remaining = 32767;
    DWORD executable_length;
    STARTUPINFOW startup = { sizeof(startup) };
    PROCESS_INFORMATION process;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION job_limits = { 0 };
    HANDLE job = NULL;
    DWORD code = 1;
    (void)instance; (void)previous; (void)ignored; (void)show;

    job = CreateJobObjectW(NULL, L"TerraNore.Build");
    if (!job) {
        MessageBoxW(NULL, L"Не удалось создать Windows Job Object.",
                    L"TerraNore: ошибка запуска", MB_OK | MB_ICONERROR);
        return 5;
    }
    job_limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                 &job_limits, sizeof(job_limits))) {
        CloseHandle(job);
        MessageBoxW(NULL, L"Не удалось настроить Windows Job Object.",
                    L"TerraNore: ошибка запуска", MB_OK | MB_ICONERROR);
        return 5;
    }

    arguments = CommandLineToArgvW(GetCommandLineW(), &argument_count);
    if (!arguments || argument_count < 1) {
        CloseHandle(job);
        return 2;
    }
    executable_length = GetModuleFileNameW(NULL, executable, 32768);
    if (!executable_length || executable_length >= 32768) {
        LocalFree(arguments);
        CloseHandle(job);
        return 2;
    }
    slash = executable + lstrlenW(executable);
    while (slash > executable && slash[-1] != L'\\' && slash[-1] != L'/') --slash;
    *slash = L'\0';
    if (lstrlenW(executable) + 9 >= 32768) {
        LocalFree(arguments);
        CloseHandle(job);
        return 3;
    }
    lstrcpyW(script, executable);
    lstrcatW(script, L"build.py");

    command = (wchar_t *)LocalAlloc(LMEM_FIXED, remaining * sizeof(wchar_t));
    if (!command) {
        LocalFree(arguments);
        CloseHandle(job);
        return 3;
    }
    cursor = command;
    if (!append_argument(&cursor, &remaining, L"py") ||
        !append_character(&cursor, &remaining, L' ') ||
        !append_argument(&cursor, &remaining, L"-3") ||
        !append_character(&cursor, &remaining, L' ') ||
        !append_argument(&cursor, &remaining, script)) goto command_too_long;
    for (index = 1; index < argument_count; ++index) {
        if (!append_character(&cursor, &remaining, L' ') ||
            !append_argument(&cursor, &remaining, arguments[index])) goto command_too_long;
    }
    *cursor = L'\0';
    LocalFree(arguments);

    if (!CreateProcessW(NULL, command, NULL, NULL, TRUE,
                        CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP,
                        NULL, executable,
                        &startup, &process)) {
        LocalFree(command);
        CloseHandle(job);
        MessageBoxW(NULL, L"Python 3 не найден. Установите Python 3.10 или новее.",
                    L"TerraNore: ошибка сборки", MB_OK | MB_ICONERROR);
        return 4;
    }
    LocalFree(command);
    if (!AssignProcessToJobObject(job, process.hProcess)) {
        TerminateProcess(process.hProcess, 5);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        CloseHandle(job);
        MessageBoxW(NULL, L"Не удалось добавить процесс сборки в Windows Job Object.",
                    L"TerraNore: ошибка запуска", MB_OK | MB_ICONERROR);
        return 5;
    }
    if (ResumeThread(process.hThread) == (DWORD)-1) {
        TerminateProcess(process.hProcess, 6);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread);
        CloseHandle(process.hProcess);
        CloseHandle(job);
        MessageBoxW(NULL, L"Не удалось возобновить процесс сборки.",
                    L"TerraNore: ошибка запуска", MB_OK | MB_ICONERROR);
        return 6;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    CloseHandle(job);
    return (int)code;

command_too_long:
    LocalFree(command);
    LocalFree(arguments);
    CloseHandle(job);
    MessageBoxW(NULL, L"Командная строка слишком длинная.",
                L"TerraNore: ошибка сборки", MB_OK | MB_ICONERROR);
    return 3;
}
