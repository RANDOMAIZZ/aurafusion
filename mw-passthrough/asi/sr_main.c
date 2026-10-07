/* sr_main.c - SnowRunner VERSION.dll proxy + passthrough core (x64).
 * Mode file C:\dev\passthrough\sr_mode.txt: probe | finder | link
 * Finder commands via C:\dev\passthrough\sr_cmd.txt, results to sr_out.txt:
 *   snap1 | snap2 | diff | read <hexaddr> | ptr <hexaddr> [depth] | base
 * All static addresses reported as SnowRunner.exe+offset (ASLR-safe).
 */
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

static HMODULE g_real = NULL;
static FARPROC pGVIW=0,pGVISW=0,pVQVW=0,pGVIA=0,pGVISA=0,pVQVA=0;
static void load_real(void) {
  if (g_real) return;
  char sys[260]; GetSystemDirectoryA(sys,260); lstrcatA(sys,"\\version.dll");
  g_real = LoadLibraryA(sys); if (!g_real) return;
  pGVIW=GetProcAddress(g_real,"GetFileVersionInfoW"); pGVISW=GetProcAddress(g_real,"GetFileVersionInfoSizeW");
  pVQVW=GetProcAddress(g_real,"VerQueryValueW"); pGVIA=GetProcAddress(g_real,"GetFileVersionInfoA");
  pGVISA=GetProcAddress(g_real,"GetFileVersionInfoSizeA"); pVQVA=GetProcAddress(g_real,"VerQueryValueA");
}
__declspec(dllexport) BOOL WINAPI GetFileVersionInfoW(LPCWSTR a,DWORD b,DWORD c,LPVOID d){load_real();return((BOOL(WINAPI*)(LPCWSTR,DWORD,DWORD,LPVOID))pGVIW)(a,b,c,d);}
__declspec(dllexport) DWORD WINAPI GetFileVersionInfoSizeW(LPCWSTR a,LPDWORD b){load_real();return((DWORD(WINAPI*)(LPCWSTR,LPDWORD))pGVISW)(a,b);}
__declspec(dllexport) BOOL WINAPI VerQueryValueW(const LPVOID a,LPWSTR b,LPVOID*c,PUINT d){load_real();return((BOOL(WINAPI*)(LPCVOID,LPWSTR,LPVOID*,PUINT))pVQVW)(a,b,c,d);}
__declspec(dllexport) BOOL WINAPI GetFileVersionInfoA(LPCSTR a,DWORD b,DWORD c,LPVOID d){load_real();return((BOOL(WINAPI*)(LPCSTR,DWORD,DWORD,LPVOID))pGVIA)(a,b,c,d);}
__declspec(dllexport) DWORD WINAPI GetFileVersionInfoSizeA(LPCSTR a,LPDWORD b){load_real();return((DWORD(WINAPI*)(LPCSTR,LPDWORD))pGVISA)(a,b);}
__declspec(dllexport) BOOL WINAPI VerQueryValueA(const LPVOID a,LPSTR b,LPVOID*c,PUINT d){load_real();return((BOOL(WINAPI*)(LPCVOID,LPSTR,LPVOID*,PUINT))pVQVA)(a,b,c,d);}

/* ---------- finder ---------- */
typedef struct { unsigned long long base, size; unsigned char *data; } Region;
static Region *g_s1=0,*g_s2=0; static int g_n1=0,g_n2=0;
static unsigned long long g_imgbase=0, g_imgsize=0;
static HANDLE g_self = NULL;

static void img_range(void) {
  HMODULE m = GetModuleHandleA(NULL); g_imgbase = (unsigned long long)m;
  if (!m) return;
  unsigned char *b = (unsigned char*)m;
  { DWORD pe = *(DWORD*)(b+0x3C); DWORD nsec = *(WORD*)(b+pe+6); DWORD oh = *(WORD*)(b+pe+0x14);
    unsigned char *sec = b + pe + 0x18 + oh;
    unsigned long long end = 0;
    int i; for (i=0;i<(int)nsec && i<64;i++) {
      DWORD vs = *(DWORD*)(sec+i*40+8); DWORD va = *(DWORD*)(sec+i*40+12);
      if ((unsigned long long)va+vs > end) end = (unsigned long long)va+vs;
    }
    g_imgsize = end;
  }
}
static int writable(unsigned long p) {
  p &= 0xFF;
  return (p==0x04||p==0x08||p==0x40||p==0x80||p==0xC0);
}
static void free_snap(Region *s, int n) {
  int i; if(!s) return; for(i=0;i<n;i++) if(s[i].data) free(s[i].data); free(s);
}
static int take_snap(Region **out, int *nout) {
  free_snap(*out,*nout); *out=0; *nout=0;
  unsigned long long addr = 0x10000;
  int cap = 8192, n = 0;
  Region *r = (Region*)malloc(sizeof(Region)*cap);
  MEMORY_BASIC_INFORMATION mi;
  SIZE_T got;
  while (addr < 0x7FFFFFFF0000ULL) {
    if (!VirtualQuery((void*)addr,&mi,sizeof(mi))) break;
    unsigned long long base=(unsigned long long)mi.BaseAddress, sz=mi.RegionSize;
    if (mi.State==MEM_COMMIT && !(mi.Protect&0x100) && !(mi.Protect&0x01)
        && writable(mi.Protect) && mi.Type==MEM_PRIVATE && sz<0x20000000ULL && sz>0) {
      unsigned char *buf=(unsigned char*)malloc((size_t)sz);
      if (buf && ReadProcessMemory(g_self,(void*)base,buf,(SIZE_T)sz,&got) && got==sz) {
        if (n>=cap){cap*=2; r=(Region*)realloc(r,sizeof(Region)*cap);}
        r[n].base=base; r[n].size=sz; r[n].data=buf; n++;
      } else if (buf) free(buf);
    }
    addr = base + sz;
    if (sz==0) break;
  }
  *out=r; *nout=n; return n;
}
static void wlog(const char *s) {
  HANDLE f=CreateFileA("C:\\dev\\passthrough\\sr_out.txt",GENERIC_WRITE,FILE_SHARE_READ,NULL,OPEN_ALWAYS,0,NULL);
  if(f==INVALID_HANDLE_VALUE) return;
  SetFilePointer(f,0,NULL,FILE_END);
  DWORD w=0; WriteFile(f,s,lstrlenA(s),&w,NULL); CloseHandle(f);
}
static void fmt_addr(char *o, unsigned long long a) {
  if (g_imgbase && a>=g_imgbase && a<g_imgbase+g_imgsize)
    wsprintfA(o,"exe+0x%I64X(abs 0x%I64X)",a-g_imgbase,a);
  else wsprintfA(o,"abs 0x%I64X",a);
}
static void cmd_diff(void) {
  int i,j; char line[512],aa[128];
  int runs=0,cmps=0;
  for(i=0;i<g_n1;i++){
    for(j=0;j<g_n2;j++) if(g_s2[j].base==g_s1[i].base && g_s2[j].size==g_s1[i].size) break;
    if(j>=g_n2) continue;
    unsigned long long n = g_s1[i].size/4;
    unsigned long k; unsigned *a=(unsigned*)g_s1[i].data,*b=(unsigned*)g_s2[j].data;
    unsigned long run=0,runstart=0;
    for(k=0;k<n;k++){
      int ch = (a[k]!=b[k]);
      float fv; { unsigned u=b[k]; fv=*(float*)&u; }
      if(ch && !(fv!=fv) && fv>-100000.0f && fv<100000.0f){ if(run==0)runstart=k; run++; }
      else {
        if(run>=3 && runs<2000){
          unsigned long long ad=g_s1[i].base+(unsigned long long)runstart*4;
          fmt_addr(aa,ad);
          { float v0=*(float*)&a[runstart],v1=*(float*)&b[runstart],v2=*(float*)&b[runstart+1],v3=*(float*)&b[runstart+2];
            wsprintfA(line,"run len=%d @ %s : [%f %f %f] -> [%f %f %f]\r\n",(int)run,aa,v0,*(float*)&a[runstart+1],*(float*)&a[runstart+2],v1,v2,v3); }
          wlog(line); runs++;
        }
        run=0;
      }
      cmps++;
    }
  }
  { char s[128]; wsprintfA(s,"# diff done runs=%d\r\n",runs); wlog(s); }
}
static void cmd_read(unsigned long long ad) {
  float v[8]; int i; SIZE_T got=0;
  char line[512],aa[128]; fmt_addr(aa,ad);
  if(!ReadProcessMemory(g_self,(void*)ad,v,sizeof(v),&got)||got!=sizeof(v)){wlog("# read FAIL\r\n");return;}
  wsprintfA(line,"@ %s :",aa);
  for(i=0;i<8;i++){char t[48];wsprintfA(t," %f",v[i]);lstrcatA(line,t);}
  lstrcatA(line,"\r\n"); wlog(line);
}
static void cmd_ptr(unsigned long long target, int depth) {
  unsigned long long addr=0x10000; MEMORY_BASIC_INFORMATION mi;
  unsigned long long l1[512]; int n1=0; char line[256],aa[128],bb[128];
  SIZE_T got;
  while(addr<0x7FFFFFFF0000ULL){
    if(!VirtualQuery((void*)addr,&mi,sizeof(mi))) break;
    unsigned long long base=(unsigned long long)mi.BaseAddress,sz=mi.RegionSize;
    if(mi.State==MEM_COMMIT&&!(mi.Protect&0x100)&&!(mi.Protect&0x01)&&(mi.Protect&0x04||mi.Protect&0x02||mi.Protect&0x40||mi.Protect&0x80)){
      unsigned long long off;
      for(off=0;off+8<=sz;off+=8){
        unsigned long long v=0;
        if(ReadProcessMemory(g_self,(void*)(base+off),&v,8,&got)&&got==8){
          if(v>=target-2048 && v<=target+2048 && v!=0){
            if(n1<512)l1[n1++]=base+off;
          }
        }
      }
    }
    addr=base+sz; if(sz==0)break;
  }
  { char s[128]; wsprintfA(s,"# ptr L1 hits=%d for target ",n1); wlog(s); fmt_addr(aa,target); {char s2[160];wsprintfA(s2,"%s\r\n",aa);wlog(s2);} }
  {int i; for(i=0;i<n1 && i<120;i++){fmt_addr(aa,l1[i]); wsprintfA(line,"L1 [%s]\r\n",aa); wlog(line);}}
  if(depth>=2){
    int i; int n2=0;
    for(i=0;i<n1 && i<100;i++){
      addr=0x10000;
      while(addr<0x7FFFFFFF0000ULL){
        if(!VirtualQuery((void*)addr,&mi,sizeof(mi))) break;
        unsigned long long base=(unsigned long long)mi.BaseAddress,sz=mi.RegionSize;
        if(mi.State==MEM_COMMIT&&!(mi.Protect&0x100)&&!(mi.Protect&0x01)&&(mi.Protect&0x04||mi.Protect&0x02||mi.Protect&0x40||mi.Protect&0x80)){
          unsigned long long off;
          for(off=0;off+8<=sz;off+=8){
            unsigned long long v=0;
            if(ReadProcessMemory(g_self,(void*)(base+off),&v,8,&got)&&got==8){
              if(v==l1[i]){
                if(g_imgbase&&(base+off)>=g_imgbase&&(base+off)<g_imgbase+g_imgsize){
                  fmt_addr(aa,base+off); fmt_addr(bb,l1[i]);
                  wsprintfA(line,"L2 static [%s] -> [%s]\r\n",aa,bb); wlog(line);
                  if(++n2>200) break;
                }
              }
            }
          }
          if(n2>200) break;
        }
        addr=base+sz; if(sz==0)break;
      }
      if(n2>200) break;
    }
    { char s[64]; wsprintfA(s,"# ptr L2 static=%d\r\n",n2); wlog(s); }
  }
}
static unsigned long long parse_hex(const char *s){unsigned long long v=0;sscanf(s,"%I64x",&v);sscanf(s,"0x%I64x",&v);sscanf(s,"%I64d",&v);return v;}
static DWORD WINAPI finder(LPVOID p){
  (void)p; char last[256]=""; char cmd[256];
  g_self = GetCurrentProcess(); img_range();
  { char s[160]; wsprintfA(s,"# finder ready base=0x%I64X size=0x%I64X\r\n",g_imgbase,g_imgsize); wlog(s); }
  for(;;){
    Sleep(700);
    HANDLE f=CreateFileA("C:\\dev\\passthrough\\sr_cmd.txt",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
    if(f==INVALID_HANDLE_VALUE) continue;
    DWORD r=0; cmd[0]=0; ReadFile(f,cmd,200,&r,NULL); CloseHandle(f);
    cmd[200]=0; {int i; for(i=0;cmd[i];i++) if(cmd[i]=='\r'||cmd[i]=='\n'){cmd[i]=0;break;}}
    if(!cmd[0]||!lstrcmpA(cmd,last)) continue;
    lstrcpyA(last,cmd);
    if(!lstrcmpA(cmd,"snap1")){int n=take_snap(&g_s1,&g_n1);{char s[96];wsprintfA(s,"# snap1 regions=%d\r\n",n);wlog(s);}}
    else if(!lstrcmpA(cmd,"snap2")){int n=take_snap(&g_s2,&g_n2);{char s[96];wsprintfA(s,"# snap2 regions=%d\r\n",n);wlog(s);}}
    else if(!lstrcmpA(cmd,"diff")){wlog("# diff start\r\n");cmd_diff();}
    else if(!lstrcmpA(cmd,"base")){char s[96];wsprintfA(s,"# base=0x%I64X\r\n",g_imgbase);wlog(s);}
    else if(cmd[0]=='r'&&cmd[1]=='e'&&cmd[2]=='a'&&cmd[3]=='d'&&cmd[4]==' '){cmd_read(parse_hex(cmd+5));}
    else if(cmd[0]=='p'&&cmd[1]=='t'&&cmd[2]=='r'&&cmd[3]==' '){
      char a1[64]="",a2[64]=""; int d=1;
      sscanf(cmd+4,"%63s %63s",a1,a2); if(a2[0]=='2')d=2;
      cmd_ptr(parse_hex(a1),d);
    }
  }
  return 0;
}
static DWORD WINAPI work(LPVOID p){
  (void)p; Sleep(4000);
  char mode[32]="probe"; DWORD r=0;
  HANDLE f=CreateFileA("C:\\dev\\passthrough\\sr_mode.txt",GENERIC_READ,FILE_SHARE_READ|FILE_SHARE_WRITE,NULL,OPEN_EXISTING,0,NULL);
  if(f!=INVALID_HANDLE_VALUE){ReadFile(f,mode,24,&r,NULL);CloseHandle(f);mode[24]=0;{int i;for(i=0;mode[i];i++)if(mode[i]=='\r'||mode[i]=='\n'){mode[i]=0;break;}}}
  if(!lstrcmpA(mode,"finder")) return finder(NULL);
  {
    HMODULE m=GetModuleHandleA(NULL); char name[260]="?";
    GetModuleFileNameA(m,name,260);
    HANDLE o=CreateFileA("C:\\dev\\passthrough\\sr_probe.log",GENERIC_WRITE,0,NULL,CREATE_ALWAYS,0,NULL);
    if(o!=INVALID_HANDLE_VALUE){char b[512];int n=wsprintfA(b,"sr probe alive in %s base=%p\r\n",name,(void*)m);DWORD w=0;WriteFile(o,b,n,&w,NULL);CloseHandle(o);}
  }
  return 0;
}
BOOL WINAPI DllMain(HINSTANCE h,DWORD r,LPVOID x){(void)h;(void)x;if(r==DLL_PROCESS_ATTACH){HANDLE t=CreateThread(NULL,0,work,NULL,0,NULL);if(t)CloseHandle(t);}return TRUE;}
