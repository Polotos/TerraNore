/* Native Windows launcher for build.py. No CRT or third-party runtime is used. */
#include <windows.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR ignored, int show) {
    wchar_t executable[32768], command[65536];
    wchar_t *slash;
    STARTUPINFOW startup = { sizeof(startup) };
    PROCESS_INFORMATION process;
    DWORD code = 1;
    (void)instance; (void)previous; (void)ignored; (void)show;
    if (!GetModuleFileNameW(NULL, executable, 32768)) return 2;
    slash = executable + lstrlenW(executable);
    while (slash > executable && slash[-1] != L'\\') --slash;
    *slash = L'\0';
    if (wsprintfW(command, L"py -3 \"%sbuild.py\"", executable) <= 0) return 3;
    if (!CreateProcessW(NULL, command, NULL, NULL, TRUE, 0, NULL, executable,
                        &startup, &process)) {
        MessageBoxW(NULL, L"Python 3 не найден. Установите Python 3.10 или новее.",
                    L"TerraNore: ошибка сборки", MB_OK | MB_ICONERROR);
        return 4;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)code;
}
