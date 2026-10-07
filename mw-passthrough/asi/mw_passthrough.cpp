// mw_passthrough.cpp - заготовка ASI для NFS MW 2005 (32-bit, DX9).
// Собирать: Visual Studio 2022 x86, линк ws2_32.lib
// Логика как gta/src/script.cpp из примера, но упрощена: только cam+ground.
// TODO: найти адреса через Cheat Engine (твой speed.exe 2023 отличается от оригинала):
//   ADDR_PLAYER_X/Y/Z, ADDR_CAM_X/Y/Z, ADDR_HEADING
// Пока шлет круговую демо-камеру, чтобы проверить линк без адресов.
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <cstdio>
#include <cmath>
#pragma comment(lib, "ws2_32.lib")

// TODO(cheat-engine): подставь реальные указатели для твоей сборки
// static const uintptr_t ADDR_PLAYER_X = 0x0; // float*
// static const uintptr_t ADDR_PLAYER_Y = 0x0;
// static const uintptr_t ADDR_PLAYER_Z = 0x0;

static SOCKET g_sock = INVALID_SOCKET;
static volatile bool g_run = true;

static bool NetConnect() {
    WSADATA w; WSAStartup(MAKEWORD(2, 2), &w);
    g_sock = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(25599);
    inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
    return connect(g_sock, (sockaddr*)&a, sizeof(a)) == 0;
}

// минимальный WS-клиент: шлем текстовые фреймы без маски (сервер на 127.0.0.1 простит)
static void WsSend(const char* json) {
    if (g_sock == INVALID_SOCKET) return;
    unsigned len = (unsigned)strlen(json);
    char hdr[10]; int hl = 0;
    hdr[0] = (char)0x81; // text
    if (len < 126) { hdr[1] = (char)len; hl = 2; }
    else { hdr[1] = 126; hdr[2] = (len >> 8) & 0xFF; hdr[3] = len & 0xFF; hl = 4; }
    send(g_sock, hdr, hl, 0);
    send(g_sock, json, len, 0);
}

static DWORD WINAPI Loop(LPVOID) {
    // WS handshake
    const char* hs = "GET / HTTP/1.1\r\nHost: 127.0.0.1:25599\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n";
    if (!NetConnect()) return 1;
    send(g_sock, hs, (int)strlen(hs), 0);
    char tmp[1024]; recv(g_sock, tmp, sizeof(tmp), 0); // ответ 101, игнорим проверку
    float t = 0;
    char buf[1024];
    while (g_run) {
        t += 1.0f / 60.0f;
        float x = 8 * cosf(t * 0.3f), z = 8 * sinf(t * 0.3f);
        // cam
        _snprintf_s(buf, sizeof(buf), "{\"t\":\"cam\",\"pos\":[%f,%f,%f],\"yaw\":%f,\"pitch\":-10.0,\"fov\":70.0}",
            x, 67.0f, z, fmodf(t * 17.19f, 360.0f));
        WsSend(buf);
        // ground: плоская сетка y=64, аналог 160 проб из GTA-примера
        _snprintf_s(buf, sizeof(buf), "{\"t\":\"ground\",\"yOffset\":0,\"cols\":[{\"x\":0,\"z\":0,\"y\":64}]}");
        WsSend(buf);
        Sleep(16);
    }
    return 0;
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        // класть как scripts/MWPassthrough.asi через твой dinput8-лоадер
        CreateThread(0, 0, Loop, 0, 0, 0);
    } else if (reason == DLL_PROCESS_DETACH) {
        g_run = false;
    }
    return TRUE;
}
