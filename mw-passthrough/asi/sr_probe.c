/* sr_probe.c - SnowRunner VERSION.dll proxy probe (x64).
 * Forwards the 6 version APIs to the real system DLL, spawns worker
 * thread that logs module base (ASLR check) to prove injection. */
#include <windows.h>

static HMODULE g_real = NULL;
static FARPROC pGVIW = 0, pGVISW = 0, pVQVW = 0, pGVIA = 0, pGVISA = 0, pVQVA = 0;

static void load_real(void) {
  if (g_real) return;
  char sys[260];
  GetSystemDirectoryA(sys, 260);
  lstrcatA(sys, "\\version.dll");
  g_real = LoadLibraryA(sys);
  if (!g_real) return;
  pGVIW  = GetProcAddress(g_real, "GetFileVersionInfoW");
  pGVISW = GetProcAddress(g_real, "GetFileVersionInfoSizeW");
  pVQVW  = GetProcAddress(g_real, "VerQueryValueW");
  pGVIA  = GetProcAddress(g_real, "GetFileVersionInfoA");
  pGVISA = GetProcAddress(g_real, "GetFileVersionInfoSizeA");
  pVQVA  = GetProcAddress(g_real, "VerQueryValueA");
}

__declspec(dllexport) BOOL WINAPI GetFileVersionInfoW(LPCWSTR a, DWORD b, DWORD c, LPVOID d) { load_real(); return ((BOOL(WINAPI*)(LPCWSTR,DWORD,DWORD,LPVOID))pGVIW)(a,b,c,d); }
__declspec(dllexport) DWORD WINAPI GetFileVersionInfoSizeW(LPCWSTR a, LPDWORD b) { load_real(); return ((DWORD(WINAPI*)(LPCWSTR,LPDWORD))pGVISW)(a,b); }
__declspec(dllexport) BOOL WINAPI VerQueryValueW(const LPVOID a, LPWSTR b, LPVOID* c, PUINT d) { load_real(); return ((BOOL(WINAPI*)(LPCVOID,LPWSTR,LPVOID*,PUINT))pVQVW)(a,b,c,d); }
__declspec(dllexport) BOOL WINAPI GetFileVersionInfoA(LPCSTR a, DWORD b, DWORD c, LPVOID d) { load_real(); return ((BOOL(WINAPI*)(LPCSTR,DWORD,DWORD,LPVOID))pGVIA)(a,b,c,d); }
__declspec(dllexport) DWORD WINAPI GetFileVersionInfoSizeA(LPCSTR a, LPDWORD b) { load_real(); return ((DWORD(WINAPI*)(LPCSTR,LPDWORD))pGVISA)(a,b); }
__declspec(dllexport) BOOL WINAPI VerQueryValueA(const LPVOID a, LPSTR b, LPVOID* c, PUINT d) { load_real(); return ((BOOL(WINAPI*)(LPCVOID,LPSTR,LPVOID*,PUINT))pVQVA)(a,b,c,d); }

static DWORD WINAPI work(LPVOID p) {
  (void)p;
  Sleep(4000);
  HMODULE m = GetModuleHandleA(NULL);
  char name[260] = "?";
  GetModuleFileNameA(m, name, 260);
  HANDLE f = CreateFileA("C:\\dev\\passthrough\\sr_probe.log", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
  if (f != INVALID_HANDLE_VALUE) {
    char b[512];
    int n = wsprintfA(b, "sr probe alive in %s base=%p\r\n", name, (void*)m);
    DWORD w = 0;
    WriteFile(f, b, n, &w, NULL);
    CloseHandle(f);
  }
  return 0;
}

BOOL WINAPI DllMain(HINSTANCE h, DWORD r, LPVOID x) {
  (void)h; (void)x;
  if (r == DLL_PROCESS_ATTACH) {
    HANDLE t = CreateThread(NULL, 0, work, NULL, 0, NULL);
    if (t) CloseHandle(t);
  }
  return TRUE;
}
