/* probe.c - минимальный ASI: доказывает что tcc-DLL грузится лоадером */
#include <windows.h>

static DWORD WINAPI work(LPVOID p) {
  (void)p;
  Sleep(3000);
  HMODULE m = GetModuleHandleA(NULL);
  DWORD ok = 0;
  if (m) {
    DWORD pe = *(DWORD*)((BYTE*)m + 0x3C);
    DWORD base = *(DWORD*)((BYTE*)m + pe + 0x34);
    ok = (base == 0x00400000);
  }
  HANDLE f = CreateFileA("C:\\dev\\passthrough\\probe.log", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
  if (f != INVALID_HANDLE_VALUE) {
    char b[128];
    int n = wsprintfA(b, "probe alive, speed.exe=%d\r\n", (int)ok);
    DWORD w = 0;
    WriteFile(f, b, n, &w, NULL);
    CloseHandle(f);
  }
  return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD r, LPVOID x) {
  (void)h; (void)x;
  if (r == DLL_PROCESS_ATTACH) {
    HANDLE t;
    t = CreateThread(NULL, 0, work, NULL, 0, NULL);
    if (t) CloseHandle(t);
  }
  return TRUE;
}
