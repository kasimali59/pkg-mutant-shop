/* PKG MUTANT SHOP - the PS4 on-console payload.
 *
 * WHAT THIS IS. The PS5 half of this app is ps5-app/onconsole/server.c: a payload that serves the
 * shop's web UI and a JSON API on :8710 and installs packages. This is the same idea for a
 * jailbroken PS4 (FW 13.52, GoldHEN). It is a SEPARATE BINARY - a PS5 prospero ELF simply does not
 * run on a PS4 (measured: GoldHEN accepts it, reports "launched successfully", and nothing
 * happens) - but it deliberately speaks THE SAME HTTP API, so the one web UI and the one PC
 * companion drive either console without caring which is on the other end.
 *
 * WHY IT IS A SEPARATE FILE RATHER THAN #ifdefs IN server.c. The PS5 file is shipping, dense, and
 * full of hard-won facts whose comments record what each arrangement cost to learn. Interleaving a
 * second console's kernel work into it is how you break a working app. Nothing here touches it.
 * The one piece borrowed is the SQLite reader, copied verbatim into sqmini.h (see that file).
 *
 * WHAT IS DIFFERENT FROM THE PS5, AND WHY:
 *
 *   Install. The PS5 lane spawns a helper process that calls sceAppInstUtilInstallByPackage,
 *   because on PS5 that call fails from an injected payload. That function DOES NOT EXIST on PS4.
 *   The PS4 route is BGFT: register a download task pointing at the companion's HTTP URL and start
 *   it; the console downloads and installs it itself, with its own progress and its own toasts.
 *   That is the same shape as the PS5 lane from the companion's side - we serve the bytes, the
 *   console pulls them - which is why the API can stay the same. See bgft.h for where every
 *   signature came from; none of it is guessed.
 *
 *   No kernel read/write. GoldHEN injects us into a process it has already jailbroken, so we start
 *   as root - but it provides no kexec syscall, so the SDK's kernel helpers are unavailable (see
 *   sdk-goldhen.patch.py). Everything here is ordinary userland: sockets, libc, and sce* calls.
 *   The consequence is that the cheat/patch engine, which writes another process's memory through
 *   a page-table walk, has NO PS4 equivalent yet and is not pretended at: the routes answer a
 *   plain "not available on this console" rather than silently doing nothing.
 *
 *   No backups/mount lane. A PS4 game is a PKG. There is no ShadowMount, no .ffpfsc, and no
 *   "runs in place from a drive" concept, so none of that exists here.
 *
 * WHAT IT SHARES WITH THE PS5 BUILD: the web UI (same web/ bundle, same generator), the API route
 * names, the file API so the companion can move files over HTTP instead of FTP, the app.db-derived
 * library, the icon server, and the notification style.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <errno.h>
#include <pthread.h>
#include <dlfcn.h>
#include <time.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <signal.h>
#include <ps4/klog.h>

#include "sqmini.h"
#include "bgft.h"
#include "web_bundle.h"
/* THE PACKAGE THIS ELF INSTALLS - except in the LITE build, which is the copy that travels
   INSIDE that package so the home-screen app can start the shop by itself. Lite is how the
   cycle is broken: package carries the lite payload, full payload carries the package. Three
   stages, no growth, and the only thing lite gives up is installing the package - which the app
   carrying it has by definition already been installed for. See ps4-app/build-all-wsl.sh. */
#ifndef PMS_LITE
#include "tile_bundle.h"
#endif

#ifndef PORT
#define PORT 8710
#endif
#define SHOP_VERSION "3.62.0"

#define SHOP_DATA_DIR  "/data/pkg-mutant-shop"
#define WEB_ROOT       SHOP_DATA_DIR "/web"
#define INSTALL_LOG    SHOP_DATA_DIR "/install.log"
#define APP_DB_PATH    "/system_data/priv/mms/app.db"
#define APPMETA_ROOT   "/user/appmeta"
/* The shop's own dashboard app. Same title id as the PS5 tile on purpose - one app, one identity,
   whichever console it is on - and it cannot collide with a game, which is CUSA or NPXS. */
#define PS4_TILE_TID   "PKGM00001"

/* Where an installed PS4 game's data actually lands. app.pkg WITH BYTES here is the only honest
   proof a title is installed - /user/appmeta survives an uninstall and would lie (the PS5 side
   learned that the hard way: 53 phantom installs). */
static const char *APP_ROOTS[] = { "/user/app", "/mnt/usb0/user/app", "/mnt/usb1/user/app", NULL };

/* ------------------------------------------------------------------ logging */

static pthread_mutex_t g_log_lock = PTHREAD_MUTEX_INITIALIZER;

static void ilog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void ilog(const char *fmt, ...) {
    char line[900];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    klog_printf("[PMS] %s\n", line);
    pthread_mutex_lock(&g_log_lock);
    int fd = open(INSTALL_LOG, O_WRONLY | O_CREAT | O_APPEND, 0777);
    if (fd >= 0) {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        char out[1000];
        int n = snprintf(out, sizeof(out), "%lld %s\n", (long long)ts.tv_sec, line);
        if (n > 0) { ssize_t w = write(fd, out, (size_t)n); (void)w; }
        close(fd);
    }
    pthread_mutex_unlock(&g_log_lock);
}

/* ------------------------------------------------------------- notifications
   Same struct and same call as the PS5 build, and the same rule: the PLAIN form only. The icon
   form returns success and draws nothing, which is how a release once shipped completely silent. */

typedef struct notify_request {
    char useless1[45];
    char message[3075];
} notify_request_t;
_Static_assert(sizeof(notify_request_t) == 0xC30, "notification struct must stay 0xC30");

int sceKernelSendNotificationRequest(int, notify_request_t *, size_t, int);
int sceKernelLoadStartModule(const char *, size_t, const void *, unsigned, void *, int *);
int sceUserServiceInitialize(void *);
int sceUserServiceGetForegroundUser(int *);
int sceUserServiceGetInitialUser(int *);
int sceAppInstUtilInitialize(void);

static void notify(const char *msg) {
    notify_request_t req;
    memset(&req, 0, sizeof(req));
    snprintf(req.message, sizeof(req.message), "%s", msg);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

static void notifyf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void notifyf(const char *fmt, ...) {
    char m[1200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(m, sizeof(m), fmt, ap);
    va_end(ap);
    notify(m);
}

/* --------------------------------------------------------------- http output */

/* Returns 0 when every byte went out, -1 when the far end stopped reading. Most callers are
   writing a short JSON answer and have nothing to do about a failure, so they ignore it; the
   package stream does care, because carrying on would mean pushing gigabytes into a socket nobody
   is holding. With SIGPIPE ignored (see main) that failure arrives as EPIPE instead of killing the
   process we are injected into. */
static int write_all(int fd, const char *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t w = write(fd, buf + off, len - off);
        if (w <= 0) return -1;
        off += (size_t)w;
    }
    return 0;
}

static void send_status(int fd, const char *status, const char *ctype, const char *body) {
    char hdr[512];
    int n = snprintf(hdr, sizeof(hdr),
                     "HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %zu\r\n"
                     "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n",
                     status, ctype, strlen(body));
    write_all(fd, hdr, (size_t)n);
    write_all(fd, body, strlen(body));
}

static void send_json(int fd, const char *body) {
    send_status(fd, "200 OK", "application/json", body);
}

static const char *ctype_for(const char *path) {
    const char *d = strrchr(path, '.');
    if (!d) return "application/octet-stream";
    if (!strcmp(d, ".html")) return "text/html; charset=utf-8";
    if (!strcmp(d, ".js"))   return "application/javascript";
    if (!strcmp(d, ".css"))  return "text/css";
    if (!strcmp(d, ".png"))  return "image/png";
    if (!strcmp(d, ".webp")) return "image/webp";
    if (!strcmp(d, ".jpg") || !strcmp(d, ".jpeg")) return "image/jpeg";
    if (!strcmp(d, ".svg"))  return "image/svg+xml";
    if (!strcmp(d, ".ico"))  return "image/x-icon";
    if (!strcmp(d, ".json")) return "application/json";
    return "application/octet-stream";
}

/* Artwork is immutable for a title and is the expensive thing to re-fetch; the app shell must
   always revalidate or loading a new build leaves the old UI cached. Same policy as the PS5. */
static int send_file(int fd, const char *path) {
    int f = open(path, O_RDONLY);
    if (f < 0) return -1;
    struct stat st;
    if (fstat(f, &st) != 0 || !S_ISREG(st.st_mode)) { close(f); return -1; }
    const char *ct = ctype_for(path);
    int is_img = ct && (strstr(ct, "image/") || strstr(ct, "font"));
    char hdr[640];
    int n = snprintf(hdr, sizeof(hdr),
                     "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %lld\r\n"
                     "Cache-Control: %s\r\nETag: \"%llx-%llx\"\r\n"
                     "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n",
                     ct, (long long)st.st_size,
                     is_img ? "public, max-age=604800, immutable" : "no-cache, must-revalidate",
                     (unsigned long long)st.st_size, (unsigned long long)st.st_mtime);
    write_all(fd, hdr, (size_t)n);
    char buf[65536];
    for (;;) {
        ssize_t r = read(f, buf, sizeof(buf));
        if (r <= 0) break;
        write_all(fd, buf, (size_t)r);
    }
    close(f);
    return 0;
}

/* --------------------------------------------------------------- json/string */

/* Keeps valid UTF-8 (so "Bloodborne™" survives) and drops only genuinely broken bytes. */
static void json_escape(const char *in, char *out, size_t outsz) {
    size_t j = 0;
    for (size_t i = 0; in[i] && j < outsz - 2; i++) {
        unsigned char c = (unsigned char)in[i];
        if (c == '"' || c == '\\') { out[j++] = '\\'; out[j++] = (char)c; }
        else if (c == '\n' || c == '\r' || c == '\t') out[j++] = ' ';
        else if (c < 0x20) continue;
        else if (c >= 0x80) {
            int need = (c >= 0xF0) ? 3 : (c >= 0xE0) ? 2 : (c >= 0xC2) ? 1 : -1;
            if (need < 0) continue;
            int ok = 1;
            for (int k = 1; k <= need; k++)
                if (((unsigned char)in[i + k] & 0xC0) != 0x80) { ok = 0; break; }
            if (!ok) continue;
            if (j + (size_t)need + 1 >= outsz - 2) break;
            out[j++] = (char)c;
            for (int k = 1; k <= need; k++) out[j++] = in[i + k];
            i += (size_t)need;
        } else out[j++] = (char)c;
    }
    out[j] = 0;
}

static int hexval(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static void url_decode(const char *in, char *out, size_t outsz) {
    size_t j = 0;
    for (size_t i = 0; in[i] && j < outsz - 1; i++) {
        if (in[i] == '%' && hexval(in[i + 1]) >= 0 && hexval(in[i + 2]) >= 0) {
            out[j++] = (char)((hexval(in[i + 1]) << 4) | hexval(in[i + 2]));
            i += 2;
        } else if (in[i] == '+') out[j++] = ' ';
        else out[j++] = in[i];
    }
    out[j] = 0;
}

/* ?key=value out of a raw request path, URL-decoded. */
static int qparam(const char *raw, const char *key, char *out, size_t outsz) {
    const char *q = strchr(raw, '?');
    if (!q) return 0;
    size_t klen = strlen(key);
    for (const char *p = q + 1; p && *p;) {
        const char *amp = strchr(p, '&');
        if (!strncmp(p, key, klen) && p[klen] == '=') {
            const char *v = p + klen + 1;
            size_t vlen = amp ? (size_t)(amp - v) : strlen(v);
            char enc[1200];
            if (vlen >= sizeof(enc)) vlen = sizeof(enc) - 1;
            memcpy(enc, v, vlen);
            enc[vlen] = 0;
            url_decode(enc, out, outsz);
            return 1;
        }
        if (!amp) break;
        p = amp + 1;
    }
    return 0;
}

static const char *strcasestr_local(const char *hay, const char *needle) {
    size_t n = strlen(needle);
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < n) {
            char a = hay[i], b = needle[i];
            if (a >= 'A' && a <= 'Z') a = (char)(a - 'A' + 'a');
            if (b >= 'A' && b <= 'Z') b = (char)(b - 'A' + 'a');
            if (a != b) break;
            i++;
        }
        if (i == n) return hay;
    }
    return NULL;
}

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static char *slurp(const char *path, long *out_len) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st) != 0 || st.st_size <= 0) { close(fd); return NULL; }
    char *buf = (char *)malloc((size_t)st.st_size + 1);
    if (!buf) { close(fd); return NULL; }
    long got = 0;
    while (got < st.st_size) {
        ssize_t r = read(fd, buf + got, (size_t)(st.st_size - got));
        if (r <= 0) break;
        got += r;
    }
    close(fd);
    buf[got] = 0;
    if (out_len) *out_len = got;
    return buf;
}

static void mkparents(const char *path) {
    char tmp[512];
    snprintf(tmp, sizeof(tmp), "%s", path);
    for (char *p = tmp + 1; *p; p++)
        if (*p == '/') { *p = 0; mkdir(tmp, 0777); *p = '/'; }
}

static const char *lan_ip_str(void) {
    static char ip[24] = "127.0.0.1";
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s < 0) return ip;
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = inet_addr("8.8.8.8");
    a.sin_port = htons(53);
    if (connect(s, (struct sockaddr *)&a, sizeof(a)) == 0) {
        struct sockaddr_in me;
        socklen_t l = sizeof(me);
        if (getsockname(s, (struct sockaddr *)&me, &l) == 0)
            snprintf(ip, sizeof(ip), "%s", inet_ntoa(me.sin_addr));
    }
    close(s);
    return ip;
}

/* ------------------------------------------------------- the PS4's app.db
 *
 * DIFFERENT SCHEMA FROM THE PS5, same path. The PS5 keeps one tbl_contentinfo; the PS4 keeps a
 * per-user tbl_appbrowse_<userid> (titleId, contentId, titleName, contentSize, category, ...)
 * plus a key/value tbl_appinfo (titleId, key, val) where the installed version lives under
 * APP_VER - which is exactly the field the companion already prefers for CUSA titles.
 *
 * category 'gd' is a real installed game; 'gdi' is a system stub with size 0 (Destiny, Spotify,
 * PS Now and friends ship as those and must never be listed as installed games).
 */

#define MAX_TITLES 512

typedef struct {
    char tid[16];
    char cid[64];
    char name[160];
    char ver[16];
    long long size;
    char category[8];
} ps4_title_t;

typedef struct {
    ps4_title_t *rows;
    int n, max;
    int i_tid, i_cid, i_name, i_size, i_cat;
} browse_ctx_t;

static void browse_cb(void *ctx, int ncol, char **v) {
    browse_ctx_t *c = (browse_ctx_t *)ctx;
    if (c->n >= c->max) return;
    if (c->i_tid < 0 || c->i_tid >= ncol) return;
    const char *tid = v[c->i_tid];
    if (!tid || strlen(tid) < 9) return;
    /* games only: CUSA/NPXS-style ids that are real content */
    const char *cat = (c->i_cat >= 0 && c->i_cat < ncol) ? v[c->i_cat] : "";
    if (!cat || strncmp(cat, "gd", 2)) return;      /* gd = installed game, gdi = system stub */
    if (!strcmp(cat, "gdi")) return;
    ps4_title_t *t = &c->rows[c->n];
    memset(t, 0, sizeof(*t));
    snprintf(t->tid, sizeof(t->tid), "%.15s", tid);
    snprintf(t->category, sizeof(t->category), "%.7s", cat);
    if (c->i_cid >= 0 && c->i_cid < ncol && v[c->i_cid]) snprintf(t->cid, sizeof(t->cid), "%.63s", v[c->i_cid]);
    if (c->i_name >= 0 && c->i_name < ncol && v[c->i_name]) snprintf(t->name, sizeof(t->name), "%.159s", v[c->i_name]);
    if (c->i_size >= 0 && c->i_size < ncol && v[c->i_size]) t->size = atoll(v[c->i_size]);
    c->n++;
}

typedef struct {
    ps4_title_t *rows;
    int n;
    int i_tid, i_key, i_val;
} appinfo_ctx_t;

static void appinfo_cb(void *ctx, int ncol, char **v) {
    appinfo_ctx_t *c = (appinfo_ctx_t *)ctx;
    if (c->i_tid < 0 || c->i_key < 0 || c->i_val < 0) return;
    if (c->i_tid >= ncol || c->i_key >= ncol || c->i_val >= ncol) return;
    const char *tid = v[c->i_tid], *key = v[c->i_key], *val = v[c->i_val];
    if (!tid || !key || !val) return;
    if (strcmp(key, "APP_VER")) return;             /* the installed version, PS4 spelling */
    for (int i = 0; i < c->n; i++)
        if (!strcmp(c->rows[i].tid, tid)) { snprintf(c->rows[i].ver, sizeof(c->rows[i].ver), "%.15s", val); return; }
}

/* Walk sqlite_master for a table whose name starts with `prefix`, then scan it. */
typedef struct { const char *prefix; char name[96]; char sql[4096]; uint32_t root; } find_ctx_t;

static void find_table_cb(void *ctx, int ncol, char **v) {
    find_ctx_t *f = (find_ctx_t *)ctx;
    if (ncol < 5 || f->root) return;
    if (!v[0] || strcmp(v[0], "table")) return;
    if (!v[1] || strncmp(v[1], f->prefix, strlen(f->prefix))) return;
    snprintf(f->name, sizeof(f->name), "%.95s", v[1]);
    snprintf(f->sql, sizeof(f->sql), "%.4095s", v[4] ? v[4] : "");
    f->root = (uint32_t)atoi(v[3] ? v[3] : "0");
}

/* Read the console's own list of installed titles. Returns how many were filled in. */
static int read_console_titles(ps4_title_t *out, int max) {
    long len = 0;
    char *data = slurp(APP_DB_PATH, &len);
    if (!data) return 0;
    sqdb_t db;
    if (sq_open(&db, (const uint8_t *)data, (size_t)len) != 0) { free(data); return 0; }

    find_ctx_t fb = { "tbl_appbrowse_", {0}, {0}, 0 };
    sq_walk(&db, 1, 5, find_table_cb, &fb, 0);
    if (!fb.root) { free(data); return 0; }

    browse_ctx_t bc;
    memset(&bc, 0, sizeof(bc));
    bc.rows = out; bc.max = max;
    bc.i_tid  = sq_col_index(fb.sql, "titleId");
    bc.i_cid  = sq_col_index(fb.sql, "contentId");
    bc.i_name = sq_col_index(fb.sql, "titleName");
    bc.i_size = sq_col_index(fb.sql, "contentSize");
    bc.i_cat  = sq_col_index(fb.sql, "category");
    int ncol = bc.i_cat + 1;
    for (int i = 0; i < 6; i++) { /* widen to the largest index we asked for */ }
    if (bc.i_tid + 1 > ncol) ncol = bc.i_tid + 1;
    if (bc.i_cid + 1 > ncol) ncol = bc.i_cid + 1;
    if (bc.i_name + 1 > ncol) ncol = bc.i_name + 1;
    if (bc.i_size + 1 > ncol) ncol = bc.i_size + 1;
    if (ncol < 1) { free(data); return 0; }
    sq_walk(&db, fb.root, ncol, browse_cb, &bc, 0);

    /* second pass: the installed version out of the key/value table */
    find_ctx_t fa = { "tbl_appinfo", {0}, {0}, 0 };
    sq_walk(&db, 1, 5, find_table_cb, &fa, 0);
    if (fa.root) {
        appinfo_ctx_t ac;
        memset(&ac, 0, sizeof(ac));
        ac.rows = out; ac.n = bc.n;
        ac.i_tid = sq_col_index(fa.sql, "titleId");
        ac.i_key = sq_col_index(fa.sql, "key");
        ac.i_val = sq_col_index(fa.sql, "val");
        int an = ac.i_tid;
        if (ac.i_key > an) an = ac.i_key;
        if (ac.i_val > an) an = ac.i_val;
        if (an >= 0) sq_walk(&db, fa.root, an + 1, appinfo_cb, &ac, 0);
    }
    free(data);
    return bc.n;
}

/* app.pkg WITH BYTES is the only honest proof. Returns its size, or 0. */
static long long installed_app_pkg(const char *tid) {
    for (int i = 0; APP_ROOTS[i]; i++) {
        char p[600];
        struct stat st;
        snprintf(p, sizeof(p), "%s/%s/app.pkg", APP_ROOTS[i], tid);
        if (stat(p, &st) == 0 && S_ISREG(st.st_mode) && st.st_size > 0) return (long long)st.st_size;
    }
    return 0;
}

static int icon_path_for(const char *tid, char *out, size_t outsz) {
    struct stat st;
    snprintf(out, outsz, "%s/%s/icon0.png", APPMETA_ROOT, tid);
    if (stat(out, &st) == 0 && S_ISREG(st.st_mode)) return 1;
    for (int i = 0; APP_ROOTS[i]; i++) {
        snprintf(out, outsz, "%s/%s/sce_sys/icon0.png", APP_ROOTS[i], tid);
        if (stat(out, &st) == 0 && S_ISREG(st.st_mode)) return 1;
    }
    out[0] = 0;
    return 0;
}

/* ------------------------------------------------------------ the PC peers
   Each companion announces itself; we keep them all and hand the list to the page so it can pick
   one. Same contract as the PS5 build's /api/companion and /api/register-pc. */

#define PC_MAX 8
typedef struct { char ip[24]; int port; char name[64]; long long last_ms; char ver[16]; } pcpeer_t;
static pcpeer_t g_pcs[PC_MAX];
static pthread_mutex_t g_pcs_lock = PTHREAD_MUTEX_INITIALIZER;

static void pc_register(const char *ip, int port, const char *name, const char *ver) {
    if (!ip || !*ip || port <= 0) return;
    pthread_mutex_lock(&g_pcs_lock);
    int slot = -1, oldest = 0;
    for (int i = 0; i < PC_MAX; i++) {
        if (!g_pcs[i].ip[0]) { if (slot < 0) slot = i; continue; }
        if (!strcmp(g_pcs[i].ip, ip)) { slot = i; break; }
        if (g_pcs[i].last_ms < g_pcs[oldest].last_ms) oldest = i;
    }
    if (slot < 0) slot = oldest;
    snprintf(g_pcs[slot].ip, sizeof(g_pcs[slot].ip), "%.23s", ip);
    g_pcs[slot].port = port;
    if (name && *name) snprintf(g_pcs[slot].name, sizeof(g_pcs[slot].name), "%.63s", name);
    if (ver && *ver)   snprintf(g_pcs[slot].ver, sizeof(g_pcs[slot].ver), "%.15s", ver);
    g_pcs[slot].last_ms = now_ms();
    pthread_mutex_unlock(&g_pcs_lock);
}

/* ---------------------------------------------------------- the install lane
 *
 * BGFT. We hand the console a URL on the companion and it downloads and installs by itself.
 * Every symbol is resolved by name at startup; a missing one means this route refuses in words
 * rather than crashing. See bgft.h for where the signatures come from.
 */

static pfn_bgft_init_t       bgft_init_fn;
static pfn_bgft_register_t   bgft_register_fn;
static pfn_bgft_start_t      bgft_start_fn;
static pfn_bgft_stop_t       bgft_stop_fn;
static pfn_bgft_unregister_t bgft_unreg_fn;
static pfn_bgft_progress_t   bgft_progress_fn;
static int  g_bgft_ready = 0;
static void *g_bgft_heap = NULL;
#define BGFT_HEAP_SIZE (1 * 1024 * 1024)

/* the one job we are following, so /api/engine/job can answer the companion */
static pthread_mutex_t g_job_lock = PTHREAD_MUTEX_INITIALIZER;
static struct {
    int  active;
    OrbisBgftTaskId task;
    char tid[16];
    char cid[64];
    char name[160];
    char uri[1024];
    long long job_id;
    long long started_ms;
    long long done, total;
    long long expect;      /* the package size the companion told us, for the finished check */
    long long seen_total;  /* the widest total BGFT ever claimed for this job - see job_refresh */
    int  released;         /* its BGFT task has been handed back - do it once, not per poll */
    char state[24];        /* idle | downloading | installed | error */
    char msg[256];
    unsigned rc;
} g_job;

/* Handles from dlopen, kept so we can search them by name.
   RTLD_DEFAULT ALONE IS NOT ENOUGH: measured on 13.52, every BGFT symbol came back NULL from the
   default scope and resolved fine from the handle of the library we opened. The first build of
   this file searched only RTLD_DEFAULT and refused every install with "BGFT is incomplete on this
   firmware", which was our own lookup being wrong, not the console lacking the calls. */
#define DL_MAX 8
static void *g_dl[DL_MAX];
static int   g_dl_n;

static void *dlsym_any(const char *sym) {
    void *p = dlsym(RTLD_DEFAULT, sym);
    for (int i = 0; !p && i < g_dl_n; i++) p = dlsym(g_dl[i], sym);
    return p;
}

static void bgft_bootstrap(void) {
    static const char *LIBS[] = {
        "/system/common/lib/libSceBgft.sprx",
        "/system/common/lib/libSceAppInstUtil.sprx",
        "/system/common/lib/libSceUserService.sprx",
        "/system/common/lib/libSceSystemService.sprx",
        NULL
    };
    for (int i = 0; LIBS[i]; i++) {
        int mres = 0;
        sceKernelLoadStartModule(LIBS[i], 0, 0, 0, 0, &mres);
        void *h = dlopen(LIBS[i], RTLD_LAZY | RTLD_GLOBAL);
        if (h && g_dl_n < DL_MAX) g_dl[g_dl_n++] = h;
    }
    bgft_init_fn     = (pfn_bgft_init_t)      dlsym_any("sceBgftServiceIntInit");
    bgft_register_fn = (pfn_bgft_register_t)  dlsym_any("sceBgftServiceIntDebugDownloadRegisterPkg");
    bgft_start_fn    = (pfn_bgft_start_t)     dlsym_any("sceBgftServiceIntDownloadStartTask");
    bgft_stop_fn     = (pfn_bgft_stop_t)      dlsym_any("sceBgftServiceIntDownloadStopTask");
    bgft_unreg_fn    = (pfn_bgft_unregister_t)dlsym_any("sceBgftServiceIntDownloadUnregisterTask");
    bgft_progress_fn = (pfn_bgft_progress_t)  dlsym_any("sceBgftServiceIntDownloadGetProgress");

    sceUserServiceInitialize(NULL);
    int air = sceAppInstUtilInitialize();

    if (!bgft_init_fn || !bgft_register_fn || !bgft_start_fn) {
        ilog("install: BGFT is incomplete on this firmware (init=%p reg=%p start=%p) - installs refused",
             (void *)bgft_init_fn, (void *)bgft_register_fn, (void *)bgft_start_fn);
        return;
    }
    g_bgft_heap = malloc(BGFT_HEAP_SIZE);
    if (!g_bgft_heap) { ilog("install: no memory for the BGFT heap"); return; }
    memset(g_bgft_heap, 0, BGFT_HEAP_SIZE);
    OrbisBgftInitParams p;
    memset(&p, 0, sizeof(p));
    p.heap = g_bgft_heap;
    p.heapSize = BGFT_HEAP_SIZE;
    int rc = bgft_init_fn(&p);
    /* A NON-ZERO INIT IS NORMAL ON A RELOAD. GoldHEN injects every payload into the SAME host
       process, so when this build replaces a previous one the service is already initialised and
       init answers 0x80990001 - measured, first load rc=0 and the very next load rc=0x80990001.
       Refusing installs on that would mean the shop only ever worked until its first update.
       So we stay ready and record the code; if the service really were unusable, the register call
       below is where it shows up, with a message that says so. */
    g_bgft_ready = 1;
    if (rc == 0)
        ilog("install: BGFT ready (init rc=0, AppInstUtil rc=0x%08X)", (unsigned)air);
    else
        ilog("install: BGFT already initialised in this process (init rc=0x%08X, AppInstUtil rc=0x%08X) - continuing",
             (unsigned)rc, (unsigned)air);
}

/* A PS4 PKG's content id sits at 0x40 in the header. We only need it to name the BGFT task. */
static int pkg_content_id_from_url_name(const char *name, char *out, size_t outsz) {
    /* The companion puts the content id in the file name when it knows it; otherwise the title id
       is enough for BGFT to accept the task. Pull a CUSAxxxxx out of whatever we were given. */
    const char *p = name;
    while (*p) {
        if ((p[0] == 'C' && p[1] == 'U' && p[2] == 'S' && p[3] == 'A') ||
            (p[0] == 'N' && p[1] == 'P' && p[2] == 'X' && p[3] == 'S')) {
            int digits = 1;
            for (int i = 4; i < 9; i++) if (p[i] < '0' || p[i] > '9') { digits = 0; break; }
            if (digits) { snprintf(out, outsz, "%.9s", p); return 1; }
        }
        p++;
    }
    out[0] = 0;
    return 0;
}

/* ------------------------------------------------- not leaving tasks behind
 *
 * BGFT keeps one directory per registered task under /user/bgft/task and that table is NOT
 * unlimited. After a handful of test installs on this console every new register started coming
 * back 0x80990086 while the directories of those dead tasks were still sitting there: a task only
 * disappears when somebody unregisters it, so a shop that does not clean up after itself ends up
 * unable to install anything at all - including the packages it installed fine an hour earlier.
 *
 * Two halves, because one is not enough on its own:
 *   - going forward, a job that reaches a terminal state releases its own task (bgft_release);
 *   - for anything already stranded, we sweep. A payload reload loses the ids we were holding, and
 *     an install interrupted by a crash or a power cut leaves a task nobody remembers, so the ids
 *     have to be recovered from the console rather than from memory.
 *
 * WHICH TASKS ARE OURS. BGFT writes the task record as plain text inside d0.pdb, download url and
 * all. Every install we start is plain http on one of two routes we own - /library/ on a companion,
 * or /pkgfile/ on this console itself - and a real PlayStation Store task is https on a Sony host.
 * Both halves must match before we touch anything, so the tasks this PS4 already had from the
 * user's own store downloads are left exactly as they are. Requiring http alone would not be
 * enough: this console's own firmware-update task is plain http too, and carries neither route.
 *
 * BOTH ROUTES, NOT JUST /library/. The first version of this matched only the companion route, so a
 * task left behind by an install from the console's own storage was invisible to the sweep and sat
 * in the table forever - the exact leak this function exists to clear.
 *
 * We never sweep the job we are currently following either; the install route refuses to start a
 * second job while one is live, so at sweep time ours is the only one that can be running.
 */
#define BGFT_TASK_ROOT "/user/bgft/task"

static int bgft_task_is_ours(const char *dir) {
    char p[320];
    snprintf(p, sizeof(p), "%s/%s/d0.pdb", BGFT_TASK_ROOT, dir);
    long n = 0;
    char *b = slurp(p, &n);
    if (!b) return 0;
    /* The record is binary with NUL-terminated strings in it, so this walks the bytes rather than
       treating the buffer as one string - a strstr would stop at the first NUL. */
    int ours = 0;
    for (long i = 0; i + 7 <= n && !ours; i++) {
        if (memcmp(b + i, "http://", 7)) continue;
        for (long j = i + 7; j < n && b[j]; j++) {
            if (j + 9 <= n && !memcmp(b + j, "/library/", 9)) { ours = 1; break; }
            if (j + 9 <= n && !memcmp(b + j, "/pkgfile/", 9)) { ours = 1; break; }
        }
    }
    free(b);
    return ours;
}

/* Stop and unregister one task. Stop first: unregistering a task that is still transferring is
   how you get a half-written package left on the drive. Both codes are ignored on purpose - a
   task that is already stopped answers non-zero and that is a success for our purposes. */
static void bgft_release(OrbisBgftTaskId task) {
    if (task == BGFT_INVALID_TASK_ID) return;
    if (bgft_stop_fn)  bgft_stop_fn(task);
    if (bgft_unreg_fn) bgft_unreg_fn(task);
}

/* Free every stranded task of ours. `keep` is the live job's task, or BGFT_INVALID_TASK_ID.
   Returns how many were released. */
static int bgft_sweep_ours(OrbisBgftTaskId keep) {
    if (!bgft_unreg_fn) return 0;
    DIR *d = opendir(BGFT_TASK_ROOT);
    if (!d) return 0;
    int freed = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        /* the directory name is the task id in hex, which is how a task we no longer hold an id
           for is addressable at all */
        char *end = NULL;
        long id = strtol(e->d_name, &end, 16);
        if (!end || *end || id < 0 || id > 0x7fffffff) continue;
        if ((OrbisBgftTaskId)id == keep) continue;
        if (!bgft_task_is_ours(e->d_name)) continue;
        bgft_release((OrbisBgftTaskId)id);
        freed++;
    }
    closedir(d);
    if (freed) ilog("install: released %d finished task%s of ours", freed, freed == 1 ? "" : "s");
    return freed;
}

/* Register + start one download-install. Returns 0 on success.
 *
 * `cid` is the package's CONTENT id (EP0786-CUSA02365_00-RIPTIDEGP2PS4001), not its title id, and
 * `size` is the real file size. Both come from the companion, which has already parsed the PKG
 * header - the console cannot read a header it has not downloaded yet. Passing a title id here
 * (which is what a filename gives you) is refused by the firmware with 0x80990008, and a zero
 * size is not enough for the task to be accepted either. */
static int bgft_install_url(const char *uri, const char *label, const char *cid, long long size,
                            const char *ptype, char *err, size_t errsz, OrbisBgftTaskId *out_task) {
    if (!g_bgft_ready) {
        snprintf(err, errsz, "This console cannot start installs - the background transfer service did not start");
        return -1;
    }
    int user_id = 0;
    if (sceUserServiceGetForegroundUser(&user_id) != 0 || user_id == 0)
        sceUserServiceGetInitialUser(&user_id);

    char fallback[16] = {0};
    if (!cid || !*cid) pkg_content_id_from_url_name(label && *label ? label : uri, fallback, sizeof(fallback));

    /* Always before registering, never after a failure. The table fills up silently and the code
       it answers with when it is full (0x80990086 here) is not something to pattern-match on - a
       sweep is cheap, idempotent, and means the shop cannot be stopped by its own history. */
    bgft_sweep_ours(BGFT_INVALID_TASK_ID);

    OrbisBgftDownloadParam p;
    memset(&p, 0, sizeof(p));
    p.userId = user_id;
    p.entitlementType = 5;
    p.id = (cid && *cid) ? cid : fallback;
    p.contentUrl = uri;
    p.contentName = (label && *label) ? label : "PKG MUTANT SHOP";
    p.iconPath = "";
    p.playgoScenarioId = "0";
    p.option = ORBIS_BGFT_TASK_OPT_DISABLE_CDN_QUERY_PARAM;
    p.packageType = (ptype && *ptype) ? ptype : "PS4GD";
    p.packageSubType = "";
    p.packageSize = (uint32_t)(size > 0 ? size : 0);

    OrbisBgftTaskId task = BGFT_INVALID_TASK_ID;
    int rc = bgft_register_fn(&p, &task);
    if (rc != 0) {
        /* NO CODE IN THE SENTENCE. It is on screen, often on a television, and it means nothing
           to the person reading it; the install log below keeps every one of them. */
        snprintf(err, errsz, "The console refused this package. The install log on the PS4 says why");
        ilog("install: register failed rc=0x%08X id=%.48s type=%s size=%lld uri=%.140s",
             (unsigned)rc, p.id ? p.id : "", p.packageType, size, uri);
        return -2;
    }
    rc = bgft_start_fn(task);
    if (rc != 0) {
        snprintf(err, errsz, "The console accepted the package but would not start it. "
                             "The install log on the PS4 says why");
        ilog("install: start failed rc=0x%08X task=%d", (unsigned)rc, (int)task);
        if (bgft_unreg_fn) bgft_unreg_fn(task);
        return -3;
    }
    if (out_task) *out_task = task;
    ilog("install: started task=%d id=%.48s uri=%.160s", (int)task, p.id ? p.id : "?", uri);
    return 0;
}

/* Refresh g_job from the console's own progress. Called by /api/engine/job. */
static void job_refresh(void) {
    pthread_mutex_lock(&g_job_lock);
    /* A FINISHED JOB IS FINISHED. Once we have handed the task back, BGFT no longer knows it and
       answers every progress call with an error - which read as "no error, no bytes yet" and
       rewrote a job that had already failed back to "downloading". The verdict is reached once. */
    if (!g_job.active || g_job.released) { pthread_mutex_unlock(&g_job_lock); return; }
    OrbisBgftTaskId task = g_job.task;
    char tid[16];
    snprintf(tid, sizeof(tid), "%s", g_job.tid);
    pthread_mutex_unlock(&g_job_lock);

    long long done = 0, total = 0;
    int have = 0;
    unsigned err = 0;
    if (bgft_progress_fn) {
        OrbisBgftTaskProgress pr;
        memset(&pr, 0, sizeof(pr));
        if (bgft_progress_fn(task, &pr) == 0) {
            done  = (long long)pr.transferredTotal;
            total = (long long)pr.lengthTotal;
            err   = (unsigned)pr.errorResult;
            have  = 1;
        }
    }
    long long onDisk = tid[0] ? installed_app_pkg(tid) : 0;

    pthread_mutex_lock(&g_job_lock);
    if (have) { g_job.done = done; g_job.total = total; }
    if (total > g_job.seen_total) g_job.seen_total = total;
    /* WHAT COUNTS AS DONE. app.pkg on disk at the expected size is the honest proof, exactly as on
       the PS5 side - the console's own progress counter is useful for showing movement but has
       been seen sitting at 0 for a transfer that had already finished, so it is never the verdict
       on its own. Where we know the size we require it; where we do not, any bytes plus a task
       that BGFT no longer reports is a finished install. */
    long long want = g_job.expect > 0 ? g_job.expect : 0;
    int big_enough = onDisk > 0 && (want <= 0 || onDisk >= (want - want / 50));
    if (err) {
        g_job.rc = err;
        snprintf(g_job.state, sizeof(g_job.state), "error");
        /* WHY IT STOPPED, WHEN WE CAN ACTUALLY TELL.
           A console that can reach PlayStation Network checks every title for an update before it
           downloads anything, and when it finds one it builds a task with a SECOND piece in it - the
           patch - which it then cannot fetch, because the package it was given came from here and
           not from the store. The fingerprint is unmistakable and it is ours to measure: BGFT asked
           for more bytes than the package contains. Measured on 13.52 with a 227,540,992-byte game
           whose store version was newer: the task total came back 238,419,968, the extra 10,878,976
           being the update, and the install ended with nothing written.
           Anything else gets the plain sentence - inventing a cause we have not checked is how a
           protective refusal once got described as something the user should fix by reloading. */
        if (onDisk <= 0 && want > 0 && g_job.seen_total > want + (1LL << 20))
            snprintf(g_job.msg, sizeof(g_job.msg),
                     "The PS4 found a newer version of this game on PlayStation Network and tried to "
                     "merge it instead of installing this package. Stop the console reaching "
                     "PlayStation Network, or install the update package as well, then try again");
        else
            snprintf(g_job.msg, sizeof(g_job.msg),
                     "The console stopped this install. The install log on the PS4 says why");
    } else if (big_enough) {
        snprintf(g_job.state, sizeof(g_job.state), "installed");
        snprintf(g_job.msg, sizeof(g_job.msg), "Installed on this PS4");
    } else {
        snprintf(g_job.state, sizeof(g_job.state), "downloading");
        snprintf(g_job.msg, sizeof(g_job.msg), "The PS4 is downloading and installing it");
    }
    /* The job is over either way: give the task back now rather than at the next install, so a
       console left alone after a finished download does not keep a slot reserved forever. */
    int over = strcmp(g_job.state, "downloading") != 0;
    OrbisBgftTaskId spent = (over && !g_job.released) ? g_job.task : BGFT_INVALID_TASK_ID;
    if (over) g_job.released = 1;
    pthread_mutex_unlock(&g_job_lock);
    if (spent != BGFT_INVALID_TASK_ID) bgft_release(spent);
}

/* ------------------------------------------- packages the console already has
 *
 * A PS4 with a stick full of PKGs has to be installable with this PC switched off - that is what
 * the PS5 build's /api/install does, and PS4 owners keep packages on USB far more often than PS5
 * owners do. THE PS5'S SHAPE IS REUSED DELIBERATELY: the package is served over HTTP and the
 * console's own installer fetches it, because that is the pipeline that produces a playable game.
 * Registering a local path with an install call instead is what left a tile that crashes on launch
 * (see the note above /api/install in ps5-app/onconsole/server.c - it cost a console).
 *
 * The PS5 needed a whole second server for this, since its shop answers on one accept loop and a
 * multi-gigabyte transfer through it would freeze the UI. Ours gives every connection its own
 * thread, so the stream rides the normal port: /pkgfile/<token>, registered below.
 */
#define LOCALPKG_MAX 16
static pthread_mutex_t g_lp_lock = PTHREAD_MUTEX_INITIALIZER;
static struct { char path[1024]; } g_lp[LOCALPKG_MAX];
static int g_lp_n;

/* Tokens rather than the path in the URL. BGFT re-requests the URL on its own schedule, so a path
   with spaces or brackets in it would have to survive re-encoding every time; and a path in a URL
   is an invitation to ask this server to open any file on the console. Returns the token, or -1. */
static int localpkg_register(const char *path) {
    pthread_mutex_lock(&g_lp_lock);
    for (int i = 0; i < g_lp_n; i++)
        if (!strcmp(g_lp[i].path, path)) { pthread_mutex_unlock(&g_lp_lock); return i; }
    int t = -1;
    if (g_lp_n < LOCALPKG_MAX) {
        t = g_lp_n++;
        snprintf(g_lp[t].path, sizeof(g_lp[t].path), "%s", path);
    }
    pthread_mutex_unlock(&g_lp_lock);
    return t;
}

static int localpkg_path(int token, char *out, size_t outsz) {
    int ok = 0;
    pthread_mutex_lock(&g_lp_lock);
    if (token >= 0 && token < g_lp_n) { snprintf(out, outsz, "%s", g_lp[token].path); ok = 1; }
    pthread_mutex_unlock(&g_lp_lock);
    return ok;
}

/* ---------------------------------------------------- reading a PKG's own header
 *
 * The console will not accept an install task without the content id, the real size and the right
 * package type, and for a package that is already on this console there is no companion to supply
 * them - so they are read out of the file, the same way companion/pkg_meta.py reads them. The PKG
 * header is BIG-endian, the param.sfo inside it is LITTLE-endian, and in an fpkg that param.sfo
 * (entry id 0x1000) is stored in the clear.
 *
 * CATEGORY is what decides the package type, and it is the file describing itself rather than a
 * guess of ours: gd = game, gp = game patch, ac = additional content. Measured across this library,
 * a base game and its update are BOTH content_type 0x1A and differ only in flag bits, so reading
 * the type out of the header alone would have called every update a base game.
 */
#define PKG_MAGIC_PS4 0x7F434E54u
#define PKG_ENTRY_PARAM_SFO 0x1000u

static unsigned be32(const unsigned char *p) {
    return ((unsigned)p[0] << 24) | ((unsigned)p[1] << 16) | ((unsigned)p[2] << 8) | (unsigned)p[3];
}
static unsigned le32(const unsigned char *p) {
    return ((unsigned)p[3] << 24) | ((unsigned)p[2] << 16) | ((unsigned)p[1] << 8) | (unsigned)p[0];
}
static unsigned le16(const unsigned char *p) {
    return ((unsigned)p[1] << 8) | (unsigned)p[0];
}

/* One key out of a param.sfo. Strings only - every field we want is one. */
static void sfo_find(const unsigned char *sfo, long n, const char *want, char *out, size_t outsz) {
    out[0] = 0;
    if (n < 0x14 || memcmp(sfo, "\0PSF", 4)) return;
    unsigned key_tbl = le32(sfo + 8), data_tbl = le32(sfo + 12), cnt = le32(sfo + 16);
    size_t wl = strlen(want);
    if (cnt > 1024) return;
    for (unsigned i = 0; i < cnt; i++) {
        long base = 0x14 + (long)i * 16;
        if (base + 16 > n) return;
        unsigned ko = le16(sfo + base), ln = le32(sfo + base + 4), doff = le32(sfo + base + 12);
        long kp = (long)key_tbl + (long)ko;
        if (kp < 0 || kp + (long)wl + 1 > n) continue;
        if (memcmp(sfo + kp, want, wl) || sfo[kp + (long)wl] != 0) continue;
        long dp = (long)data_tbl + (long)doff;
        if (dp < 0 || dp >= n) return;
        long avail = n - dp;
        long take = (long)ln < avail ? (long)ln : avail;
        if (take >= (long)outsz) take = (long)outsz - 1;
        if (take < 0) take = 0;
        memcpy(out, sfo + dp, (size_t)take);
        out[take] = 0;
        return;
    }
}

/* Fills whatever it can and returns 1 when the file really is a PS4 package. */
static int pkg_file_facts(const char *path, char *cid, size_t cidsz, char *cat, size_t catsz,
                          char *title, size_t titlesz, long long *out_size) {
    if (cid && cidsz) cid[0] = 0;
    if (cat && catsz) cat[0] = 0;
    if (title && titlesz) title[0] = 0;
    if (out_size) *out_size = 0;
    int f = open(path, O_RDONLY);
    if (f < 0) return 0;
    struct stat st;
    if (fstat(f, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size < 0x100) { close(f); return 0; }
    unsigned char head[0x80];
    if (read(f, head, sizeof(head)) != (ssize_t)sizeof(head)) { close(f); return 0; }
    if (be32(head) != PKG_MAGIC_PS4) { close(f); return 0; }
    if (out_size) *out_size = (long long)st.st_size;
    /* The content id is at 0x40 in the header, so even a package whose param.sfo we cannot reach
       still yields the one field an install task cannot do without. */
    if (cid && cidsz) {
        size_t k = 0;
        for (; k < 36 && k + 1 < cidsz && head[0x40 + k]; k++) cid[k] = (char)head[0x40 + k];
        cid[k] = 0;
    }
    unsigned ecount = be32(head + 0x10), toff = be32(head + 0x18);
    if (ecount && ecount <= 100000 &&
        (long long)toff + (long long)ecount * 32 <= (long long)st.st_size) {
        unsigned char *tbl = (unsigned char *)malloc((size_t)ecount * 32);
        if (tbl) {
            if (lseek(f, (off_t)toff, SEEK_SET) == (off_t)toff &&
                read(f, tbl, (size_t)ecount * 32) == (ssize_t)((size_t)ecount * 32)) {
                for (unsigned i = 0; i < ecount; i++) {
                    const unsigned char *e = tbl + (size_t)i * 32;
                    if (be32(e) != PKG_ENTRY_PARAM_SFO) continue;
                    unsigned off = be32(e + 16), sz = be32(e + 20);
                    if (!sz || sz > (1u << 20) || (long long)off + sz > (long long)st.st_size) break;
                    unsigned char *sfo = (unsigned char *)malloc(sz);
                    if (!sfo) break;
                    if (lseek(f, (off_t)off, SEEK_SET) == (off_t)off &&
                        read(f, sfo, sz) == (ssize_t)sz) {
                        char buf[160];
                        if (cat && catsz) {
                            sfo_find(sfo, (long)sz, "CATEGORY", buf, sizeof(buf));
                            snprintf(cat, catsz, "%s", buf);
                        }
                        if (title && titlesz) {
                            sfo_find(sfo, (long)sz, "TITLE", buf, sizeof(buf));
                            if (buf[0]) snprintf(title, titlesz, "%s", buf);
                        }
                        sfo_find(sfo, (long)sz, "CONTENT_ID", buf, sizeof(buf));
                        if (buf[0] && cid && cidsz) snprintf(cid, cidsz, "%s", buf);
                    }
                    free(sfo);
                    break;
                }
            }
            free(tbl);
        }
    }
    close(f);
    return 1;
}

/* CATEGORY -> what BGFT calls this kind of package. The same two letters, and the same mapping the
   companion uses in PS4_PACKAGE_TYPE, so a package installs identically from either side. */
static const char *pkg_type_for_category(const char *cat) {
    if (cat && !strncmp(cat, "gp", 2)) return "PS4GP";
    if (cat && !strncmp(cat, "ac", 2)) return "PS4AC";
    if (cat && !strncmp(cat, "al", 2)) return "PS4AL";
    if (cat && !strncmp(cat, "dp", 2)) return "PS4DP";
    return "PS4GD";                       /* gd, and anything we could not read */
}

static const char *kind_for_category(const char *cat) {
    if (cat && !strncmp(cat, "gp", 2)) return "update";
    if (cat && !strncmp(cat, "ac", 2)) return "dlc";
    return "base";
}

/* --------------------------------------------- packages on removable media
   /mnt/usb0..7, three levels deep. The companion asks for these through /api/library and lists
   them beside its own, so a stick is installable from any device with the PC switched off. Same
   contract as the PS5 build, whose console_usb_packages() reads exactly this. */
#define USBPKG_MAX 200

typedef struct {
    char path[1024];
    char file[256];
    char drive[12];
    char cid[64];
    char cat[16];
    char title[160];
    long long size;
} usbpkg_t;

static int usb_scan_dir(const char *dir, const char *drive, usbpkg_t *out, int max, int n, int depth) {
    if (n >= max || depth > 3) return n;
    DIR *d = opendir(dir);
    if (!d) return n;
    struct dirent *e;
    while ((e = readdir(d)) && n < max) {
        if (e->d_name[0] == '.') continue;
        char full[1024];
        snprintf(full, sizeof(full), "%s/%s", dir, e->d_name);
        struct stat st;
        if (stat(full, &st) != 0) continue;
        if (S_ISDIR(st.st_mode)) { n = usb_scan_dir(full, drive, out, max, n, depth + 1); continue; }
        size_t l = strlen(e->d_name);
        if (l < 5 || strcasecmp(e->d_name + l - 4, ".pkg")) continue;
        usbpkg_t *u = &out[n];
        memset(u, 0, sizeof(*u));
        snprintf(u->path, sizeof(u->path), "%s", full);
        snprintf(u->file, sizeof(u->file), "%.255s", e->d_name);
        snprintf(u->drive, sizeof(u->drive), "%.11s", drive);
        /* A file that is not really a PS4 package is not offered at all - better than a card that
           cannot install. This reads two small pieces of the file, not the whole thing. */
        if (!pkg_file_facts(full, u->cid, sizeof(u->cid), u->cat, sizeof(u->cat),
                            u->title, sizeof(u->title), &u->size))
            continue;
        n++;
    }
    closedir(d);
    return n;
}

static int usb_scan(usbpkg_t *out, int max) {
    int n = 0;
    for (int i = 0; i < 8 && n < max; i++) {
        char root[32], drv[12];
        snprintf(root, sizeof(root), "/mnt/usb%d", i);
        snprintf(drv, sizeof(drv), "usb%d", i);
        struct stat st;
        if (stat(root, &st) != 0 || !S_ISDIR(st.st_mode)) continue;
        n = usb_scan_dir(root, drv, out, max, n, 0);
    }
    return n;
}

/* Title id out of a content id (EP0786-CUSA02365_00-... -> CUSA02365), empty if there is none. */
static void tid_from_cid(const char *cid, char *out, size_t outsz) {
    out[0] = 0;
    const char *d = cid ? strchr(cid, '-') : NULL;
    if (!d) return;
    d++;
    size_t k = 0;
    while (d[k] && d[k] != '_' && k + 1 < outsz) { out[k] = d[k]; k++; }
    out[k] = 0;
}

/* --------------------------------------------------------------- the file API
   Same shapes as the PS5 build so the companion's fs_read/fs_list/fs_write work unchanged. */

static int fs_param_path(const char *rawpath, char *out, size_t outsz) {
    char enc[1024] = {0};
    if (!qparam(rawpath, "path", enc, sizeof(enc))) return 0;
    snprintf(out, outsz, "%s", enc);
    return out[0] == '/';
}

static void fs_send_stat(int fd, const char *path) {
    struct stat st;
    if (stat(path, &st) != 0) {
        send_status(fd, "404 Not Found", "application/json", "{\"ok\":false,\"error\":\"no such path\"}");
        return;
    }
    char esc[1100], o[1400];
    json_escape(path, esc, sizeof(esc));
    snprintf(o, sizeof(o), "{\"ok\":true,\"path\":\"%s\",\"dir\":%s,\"size\":%lld,\"mtime\":%lld}",
             esc, S_ISDIR(st.st_mode) ? "true" : "false",
             (long long)st.st_size, (long long)st.st_mtime);
    send_json(fd, o);
}

#define FS_LIST_START (1 * 1024 * 1024)
#define FS_LIST_MAX   (8 * 1024 * 1024)

static void fs_send_list(int fd, const char *path) {
    DIR *d = opendir(path);
    if (!d) {
        send_status(fd, "404 Not Found", "application/json",
                    "{\"ok\":false,\"error\":\"cannot open directory\"}");
        return;
    }
    size_t cap = FS_LIST_START, len = 0;
    char *out = (char *)malloc(cap);
    if (!out) { closedir(d); send_json(fd, "{\"ok\":false,\"error\":\"oom\"}"); return; }
    len += (size_t)snprintf(out + len, cap - len, "{\"ok\":true,\"entries\":[");
    int wrote = 0, truncated = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        char full[1200];
        snprintf(full, sizeof(full), "%s/%s", path, e->d_name);
        struct stat st;
        if (stat(full, &st) != 0) continue;
        char esc[600];
        json_escape(e->d_name, esc, sizeof(esc));
        if (len + strlen(esc) + 160 >= cap) {
            if (cap >= FS_LIST_MAX) { truncated = 1; break; }
            size_t ncap = cap * 2;
            if (ncap > FS_LIST_MAX) ncap = FS_LIST_MAX;
            char *n = (char *)realloc(out, ncap);
            if (!n) { truncated = 1; break; }
            out = n; cap = ncap;
        }
        len += (size_t)snprintf(out + len, cap - len,
                                "%s{\"name\":\"%s\",\"dir\":%s,\"size\":%lld,\"mtime\":%lld}",
                                wrote ? "," : "", esc, S_ISDIR(st.st_mode) ? "true" : "false",
                                (long long)st.st_size, (long long)st.st_mtime);
        wrote++;
    }
    closedir(d);
    len += (size_t)snprintf(out + len, cap - len, "],\"count\":%d,\"truncated\":%s}",
                            wrote, truncated ? "true" : "false");
    send_json(fd, out);
    free(out);
}

static void fs_send_read(int fd, const char *path) {
    int f = open(path, O_RDONLY);
    if (f < 0) {
        send_status(fd, "404 Not Found", "application/json", "{\"ok\":false,\"error\":\"cannot open\"}");
        return;
    }
    struct stat st;
    if (fstat(f, &st) != 0 || !S_ISREG(st.st_mode)) {
        close(f);
        send_status(fd, "400 Bad Request", "application/json",
                    "{\"ok\":false,\"error\":\"not a regular file\"}");
        return;
    }
    char hdr[300];
    int hn = snprintf(hdr, sizeof(hdr),
                      "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                      "Content-Length: %lld\r\nCache-Control: no-store\r\n"
                      "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n",
                      (long long)st.st_size);
    write_all(fd, hdr, (size_t)hn);
    char buf[65536];
    for (;;) {
        ssize_t r = read(f, buf, sizeof(buf));
        if (r <= 0) break;
        write_all(fd, buf, (size_t)r);
    }
    close(f);
}

/* Streamed upload, .part then rename - a half-written file must never look finished. */
static int fs_recv_write(int cl, const char *rawpath, const char *req, int header_len, int have) {
    char path[1024] = {0};
    if (!fs_param_path(rawpath, path, sizeof(path))) {
        send_status(cl, "400 Bad Request", "application/json",
                    "{\"ok\":false,\"error\":\"absolute ?path= required\"}");
        return 0;
    }
    const char *cl_h = strcasestr_local(req, "content-length:");
    if (!cl_h) {
        send_status(cl, "411 Length Required", "application/json",
                    "{\"ok\":false,\"error\":\"content-length required\"}");
        return 0;
    }
    long long want = atoll(cl_h + 15);
    if (want < 0) want = 0;

    char part[1100];
    snprintf(part, sizeof(part), "%s.part", path);
    mkparents(part);
    int f = open(part, O_WRONLY | O_CREAT | O_TRUNC, 0777);
    if (f < 0) {
        send_status(cl, "500 Internal Server Error", "application/json",
                    "{\"ok\":false,\"error\":\"cannot create file\"}");
        return 0;
    }
    long long got = 0;
    int failed = 0;
    if (have > 0) {
        write_all(f, req + header_len, (size_t)have);
        got = have;
    }
    char buf[65536];
    while (!failed && got < want) {
        size_t chunk = sizeof(buf);
        if ((long long)chunk > want - got) chunk = (size_t)(want - got);
        ssize_t r = read(cl, buf, chunk);
        if (r <= 0) { failed = 1; break; }
        ssize_t w = write(f, buf, (size_t)r);
        if (w != r) { failed = 1; break; }
        got += r;
    }
    close(f);
    if (failed || got != want) {
        unlink(part);
        char o[220];
        snprintf(o, sizeof(o), "{\"ok\":false,\"error\":\"short write\",\"written\":%lld,\"expected\":%lld}",
                 got, want);
        send_status(cl, "500 Internal Server Error", "application/json", o);
        return 0;
    }
    unlink(path);
    if (rename(part, path) != 0) {
        unlink(part);
        send_status(cl, "500 Internal Server Error", "application/json",
                    "{\"ok\":false,\"error\":\"rename failed\"}");
        return 0;
    }
    char esc[1100], o[1400];
    json_escape(path, esc, sizeof(esc));
    snprintf(o, sizeof(o), "{\"ok\":true,\"path\":\"%s\",\"size\":%lld}", esc, got);
    send_json(cl, o);
    return 0;
}

/* --------------------------------------------------------------- the library */

/* THESE THREE SCANS SHARE ONE BUFFER AND THIS SERVER IS THREADED.
 *
 * The PS5 payload keeps its scan buffer in a static safely because it answers on a single accept
 * loop. This one gives every connection its own thread, and a 512-entry title array is far too
 * large to put on a thread stack - so the buffers stay static and one lock covers every reader of
 * them. It is needed: the companion, the page on the television and a phone all poll /api/library,
 * and two of them landing together would have had one thread filling the array while another read
 * it. Not a hot path - the companion caches the answer for thirty seconds.
 */
static pthread_mutex_t g_scan_lock = PTHREAD_MUTEX_INITIALIZER;

/* The USB scan, remembered briefly. Every /api/library used to walk eight mount points and read
   the header of every package on them; the companion alone asks twice a minute, and the page asks
   too. A stick plugged in shows up within twenty seconds, which nobody notices - the console does.
   Guarded by g_scan_lock, like everything else that touches these buffers. */
static usbpkg_t g_usb[USBPKG_MAX];
static int g_usb_n = 0;
static long long g_usb_at = 0;
#define USB_SCAN_TTL_MS 20000

static int usb_scan_cached(void) {
    long long now = now_ms();
    if (!g_usb_at || now - g_usb_at > USB_SCAN_TTL_MS) {
        g_usb_n = usb_scan(g_usb, USBPKG_MAX);
        g_usb_at = now;
    }
    return g_usb_n;
}

static char *build_library_json_locked(void) {
    static ps4_title_t rows[MAX_TITLES];
    int n = read_console_titles(rows, MAX_TITLES);

    size_t cap = 256 * 1024, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) return NULL;
    len += (size_t)snprintf(buf + len, cap - len, "{\"ok\":true,\"platform\":\"ps4\",\"games\":[");
    int wrote = 0;
    for (int i = 0; i < n; i++) {
        long long pkg = installed_app_pkg(rows[i].tid);
        char ipath[600];
        int has_icon = icon_path_for(rows[i].tid, ipath, sizeof(ipath));
        char en[400], ec[160];
        json_escape(rows[i].name[0] ? rows[i].name : rows[i].tid, en, sizeof(en));
        json_escape(rows[i].cid, ec, sizeof(ec));
        if (len + 900 >= cap) {
            size_t ncap = cap * 2;
            char *nb = (char *)realloc(buf, ncap);
            if (!nb) break;
            buf = nb; cap = ncap;
        }
        len += (size_t)snprintf(buf + len, cap - len,
            "%s{\"title_id\":\"%s\",\"name\":\"%s\",\"content_id\":\"%s\",\"platform\":\"PS4\","
            "\"size\":%lld,\"installed\":%s,\"installed_version\":\"%s\",\"has_icon\":%s,"
            "\"on_console\":true,\"source\":\"console\"}",
            wrote ? "," : "", rows[i].tid, en, ec,
            rows[i].size, pkg > 0 ? "true" : "false", rows[i].ver, has_icon ? "true" : "false");
        wrote++;
    }

    /* PACKAGES ON A STICK, listed beside the installed games. The companion reads these out of the
       same document (its console_usb_packages filters on `source` starting "usb"), so a PS4 with a
       USB drive full of packages is installable from any device with this PC switched off - which is
       how PS4 owners actually keep their library. `install_key` is "local:<path>", exactly the form
       the PS5 build's /api/install already takes, so one shape covers both consoles. */
    {
        usbpkg_t *usb = g_usb;
        int un = usb_scan_cached();
        for (int i = 0; i < un; i++) {
            char tid[16];
            tid_from_cid(usb[i].cid, tid, sizeof(tid));
            char en[400], ec[160], ep[1400], ef[600];
            json_escape(usb[i].title[0] ? usb[i].title : usb[i].file, en, sizeof(en));
            json_escape(usb[i].cid, ec, sizeof(ec));
            json_escape(usb[i].path, ep, sizeof(ep));
            json_escape(usb[i].file, ef, sizeof(ef));
            if (len + 2600 >= cap) {
                size_t ncap = cap * 2;
                char *nb = (char *)realloc(buf, ncap);
                if (!nb) break;
                buf = nb; cap = ncap;
            }
            len += (size_t)snprintf(buf + len, cap - len,
                "%s{\"title_id\":\"%s\",\"name\":\"%s\",\"content_id\":\"%s\","
                "\"platform\":\"PS4\",\"size\":%lld,\"installed\":false,\"has_icon\":false,"
                "\"on_console\":false,\"source\":\"%s\",\"lane\":\"install\","
                "\"local_path\":\"%s\",\"file\":\"%s\",\"kind\":\"%s\","
                "\"base\":[{\"install_key\":\"local:%s\",\"file\":\"%s\",\"size\":%lld,"
                "\"kind\":\"%s\",\"content_id\":\"%s\",\"name\":\"%s\","
                "\"title_id\":\"%s\"}],\"updates\":[],\"dlc\":[],\"cheats\":[]}",
                wrote ? "," : "", tid, en, ec, usb[i].size, usb[i].drive, ep, ef,
                kind_for_category(usb[i].cat),
                ep, ef, usb[i].size, kind_for_category(usb[i].cat), ec, en, tid);
            wrote++;
        }
    }
    len += (size_t)snprintf(buf + len, cap - len, "],\"count\":%d}", wrote);
    return buf;
}

static char *build_installed_json_locked(void) {
    static ps4_title_t rows[MAX_TITLES];
    int n = read_console_titles(rows, MAX_TITLES);
    size_t cap = 32 * 1024, len = 0;
    char *buf = (char *)malloc(cap);
    if (!buf) return NULL;
    len += (size_t)snprintf(buf + len, cap - len, "{\"ok\":true,\"installed\":[");
    int wrote = 0;
    for (int i = 0; i < n; i++) {
        if (installed_app_pkg(rows[i].tid) <= 0) continue;   /* bytes, not just a folder */
        if (len + 64 >= cap) break;
        len += (size_t)snprintf(buf + len, cap - len, "%s\"%s\"", wrote ? "," : "", rows[i].tid);
        wrote++;
    }
    len += (size_t)snprintf(buf + len, cap - len, "],\"count\":%d}", wrote);
    return buf;
}

/* Drives the console really has. The PS4 has its internal HDD plus usb0..usb6. */
static void send_devices(int fd) {
    char out[2048];
    size_t len = 0;
    len += (size_t)snprintf(out + len, sizeof(out) - len, "{\"ok\":true,\"platform\":\"ps4\",\"ps5\":[");
    struct statvfs vfs;
    int wrote = 0;
    if (statvfs("/user", &vfs) == 0) {
        long long freeb = (long long)vfs.f_bavail * (long long)vfs.f_frsize;
        long long total = (long long)vfs.f_blocks * (long long)vfs.f_frsize;
        len += (size_t)snprintf(out + len, sizeof(out) - len,
                                "{\"id\":\"internal\",\"label\":\"Internal HDD\",\"free\":%lld,"
                                "\"total\":%lld,\"detected\":true}", freeb, total);
        wrote++;
    }
    for (int i = 0; i < 7; i++) {
        char p[32];
        snprintf(p, sizeof(p), "/mnt/usb%d", i);
        struct stat st;
        int detected = 0;
        long long freeb = 0, total = 0;
        if (stat(p, &st) == 0 && S_ISDIR(st.st_mode) && statvfs(p, &vfs) == 0 && vfs.f_blocks > 0) {
            detected = 1;
            freeb = (long long)vfs.f_bavail * (long long)vfs.f_frsize;
            total = (long long)vfs.f_blocks * (long long)vfs.f_frsize;
        }
        if (len + 200 >= sizeof(out)) break;
        if (detected)
            len += (size_t)snprintf(out + len, sizeof(out) - len,
                                    "%s{\"id\":\"usb%d\",\"label\":\"USB%d\",\"free\":%lld,"
                                    "\"total\":%lld,\"detected\":true}",
                                    wrote ? "," : "", i, i, freeb, total);
        else
            len += (size_t)snprintf(out + len, sizeof(out) - len,
                                    "%s{\"id\":\"usb%d\",\"label\":\"USB%d\",\"detected\":false}",
                                    wrote ? "," : "", i, i);
        wrote++;
    }
    snprintf(out + len, sizeof(out) - len, "]}");
    send_json(fd, out);
}

/* The three wrappers. Every caller goes through these; nothing takes the lock twice. */
static char *build_library_json(void) {
    pthread_mutex_lock(&g_scan_lock);
    char *r = build_library_json_locked();
    pthread_mutex_unlock(&g_scan_lock);
    return r;
}

static char *build_installed_json(void) {
    pthread_mutex_lock(&g_scan_lock);
    char *r = build_installed_json_locked();
    pthread_mutex_unlock(&g_scan_lock);
    return r;
}

static void storage_json(char *out, size_t outsz) {
    static ps4_title_t rows[MAX_TITLES];
    int n = read_console_titles(rows, MAX_TITLES);
    long long games = 0;
    int count = 0;
    for (int i = 0; i < n; i++)
        if (installed_app_pkg(rows[i].tid) > 0) { games += rows[i].size; count++; }
    struct statvfs vfs;
    long long freeb = 0, total = 0;
    if (statvfs("/user", &vfs) == 0) {
        freeb = (long long)vfs.f_bavail * (long long)vfs.f_frsize;
        total = (long long)vfs.f_blocks * (long long)vfs.f_frsize;
    }
    snprintf(out, outsz,
             "{\"ok\":true,\"reachable\":true,\"drives\":[{\"id\":\"internal\","
             "\"label\":\"Internal HDD\",\"used\":%lld,\"games_bytes\":%lld,\"free\":%lld,"
             "\"total\":%lld,\"count\":%d,\"kind\":\"console\"}]}",
             total - freeb, games, freeb, total, count);
}

static void send_storage(int fd) {
    /* Built under the lock, sent outside it: a socket write can block on a peer that has stopped
       reading, and holding the scan lock through that would stall every other request. */
    char out[600];
    pthread_mutex_lock(&g_scan_lock);
    storage_json(out, sizeof(out));
    pthread_mutex_unlock(&g_scan_lock);
    send_json(fd, out);
}

/* --------------------------------------------------------------------- routes */

static volatile int g_quit = 0;
/* The listening socket, so /api/quit can break the accept loop out of its blocking wait without
   killing the process we are injected into. -1 once it has been handed over. */
static volatile int g_srv = -1;

/* ------------------------------------------- serving a local package to the installer
 *
 * GET/HEAD /pkgfile/<token>, with byte ranges, because the installer asks for them: it HEADs first
 * for the size, then pulls the package in pieces and re-requests from where it left off. A 200-only
 * server looks like it works and then fails partway through a large install.
 */
static void serve_pkgfile(int fd, const char *path, const char *req, int head_only) {
    char tok[24] = {0};
    const char *t = path + 9;                       /* after "/pkgfile/" */
    size_t k = 0;
    while (t[k] && t[k] != '?' && t[k] != '/' && k + 1 < sizeof(tok)) { tok[k] = t[k]; k++; }
    tok[k] = 0;
    char local[1024];
    if (!tok[0] || !localpkg_path(atoi(tok), local, sizeof(local))) {
        send_status(fd, "404 Not Found", "text/plain", "no such package");
        return;
    }
    int f = open(local, O_RDONLY);
    struct stat st;
    if (f < 0 || fstat(f, &st) != 0 || !S_ISREG(st.st_mode)) {
        if (f >= 0) close(f);
        send_status(fd, "404 Not Found", "text/plain", "the package is gone");
        return;
    }
    long long size = (long long)st.st_size, start = 0, end = size - 1;
    int partial = 0;
    const char *rh = strcasestr_local(req, "range:");
    if (rh) {
        const char *eq = strchr(rh, '=');
        if (eq) {
            eq++;
            while (*eq == ' ') eq++;
            if (*eq == '-') {                        /* suffix range: the last N bytes */
                long long n = atoll(eq + 1);
                if (n > 0) { start = size - n; if (start < 0) start = 0; }
            } else {
                start = atoll(eq);
                const char *dash = strchr(eq, '-');
                if (dash && dash[1] >= '0' && dash[1] <= '9') {
                    long long e2 = atoll(dash + 1);
                    if (e2 < end) end = e2;
                }
            }
            partial = 1;
        }
    }
    if (start > end || start >= size) {
        char h[200];
        int hn = snprintf(h, sizeof(h),
                          "HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */%lld\r\n"
                          "Content-Length: 0\r\nConnection: close\r\n\r\n", size);
        write_all(fd, h, (size_t)hn);
        close(f);
        return;
    }
    long long length = end - start + 1;
    char hdr[420];
    int hn;
    if (partial)
        hn = snprintf(hdr, sizeof(hdr),
                      "HTTP/1.1 206 Partial Content\r\nContent-Type: application/octet-stream\r\n"
                      "Accept-Ranges: bytes\r\nContent-Length: %lld\r\n"
                      "Content-Range: bytes %lld-%lld/%lld\r\nConnection: close\r\n\r\n",
                      length, start, end, size);
    else
        hn = snprintf(hdr, sizeof(hdr),
                      "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n"
                      "Accept-Ranges: bytes\r\nContent-Length: %lld\r\nConnection: close\r\n\r\n",
                      length);
    write_all(fd, hdr, (size_t)hn);
    if (head_only) { close(f); return; }
    /* THE SEND TIMEOUT COMES OFF. conn_thread arms a 60 s SO_SNDTIMEO so a dead client cannot hold
       a thread forever, which is right for an API answer and wrong for this: the installer stops
       reading for as long as it takes to promote what it already has, and a write that times out
       part-way through is an install that fails for no reason the user can see. */
    struct timeval none;
    none.tv_sec = 0; none.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &none, sizeof(none));
    if (lseek(f, (off_t)start, SEEK_SET) != (off_t)start) { close(f); return; }
    char *chunk = (char *)malloc(256 * 1024);
    if (!chunk) { close(f); return; }
    long long left = length;
    while (left > 0) {
        size_t want = left > (long long)(256 * 1024) ? (size_t)(256 * 1024) : (size_t)left;
        ssize_t r = read(f, chunk, want);
        if (r <= 0) break;
        /* Stop when the reader goes away. With SIGPIPE ignored this is a plain EPIPE, and carrying
           on would spend the rest of the file writing into a socket nobody is holding. */
        if (write_all(fd, chunk, (size_t)r) != 0) break;
        left -= r;
    }
    free(chunk);
    close(f);
}

/* One string field out of a small JSON body. The shop's own page and the companion are the only
   callers, so this stays a reader rather than a parser: it finds "key" and takes the quoted value
   after the colon. */
static void json_str_field(const char *body, const char *key, char *out, size_t outsz) {
    out[0] = 0;
    if (!body || !key) return;
    char pat[64];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *k = strstr(body, pat);
    if (!k) return;
    const char *c = strchr(k + strlen(pat), ':');
    if (!c) return;
    const char *s1 = strchr(c, '"');
    if (!s1) return;
    s1++;
    size_t j = 0;
    while (*s1 && *s1 != '"' && j + 1 < outsz) {
        if (*s1 == '\\' && s1[1]) s1++;
        out[j++] = *s1++;
    }
    out[j] = 0;
}

/* Install a package that is already on this console. Returns nothing - it answers `fd` itself. */
/* Install a package that is ALREADY on this console. Returns 0 when the console has taken it;
   `err` carries a sentence either way. Split out from the route below so the ELF's own dashboard-app
   install can use the identical lane - one install path, one set of failures, one place to fix. */
static int install_local_pkg(const char *local, char *err, size_t errsz) {
    struct stat st;
    if (stat(local, &st) != 0 || !S_ISREG(st.st_mode)) {
        snprintf(err, errsz, "That package is not on the console any more");
        return -1;
    }
    char cid[64], cat[16], title[160];
    long long size = 0;
    if (!pkg_file_facts(local, cid, sizeof(cid), cat, sizeof(cat), title, sizeof(title), &size)) {
        snprintf(err, errsz, "That file is not a PS4 package");
        return -2;
    }
    if (!cid[0]) {
        snprintf(err, errsz, "That package does not carry a content id, so the console will not "
                             "accept it");
        return -3;
    }
    job_refresh();
    pthread_mutex_lock(&g_job_lock);
    int busy = g_job.active && strcmp(g_job.state, "installed") && strcmp(g_job.state, "error");
    pthread_mutex_unlock(&g_job_lock);
    if (busy) {
        snprintf(err, errsz, "An install is already running on this PS4");
        return -4;
    }
    int tok = localpkg_register(local);
    if (tok < 0) {
        snprintf(err, errsz, "Too many packages are already queued from this console - reload the "
                             "shop and try again");
        return -5;
    }
    /* THE URL HAS TO END IN .pkg, and that cost an afternoon to find. Handing the transfer service
       `.../pkgfile/0` is refused with 0x80990033 no matter what address it is on - the console's
       own log says it in words: "[BGFT] ERROR: [2239] Not supported extension." It reads the
       EXTENSION out of the URL and will not touch a package whose url does not look like one. The
       byte-identical file offered from the PC as `/library/NAME.pkg` installed first time, which is
       what made the address look like the culprit when it never was.

       The token parser on the serving side stops at the dot, so `/pkgfile/0.pkg` is still token 0.
       Loopback rather than our LAN address: both are accepted now, and 127.0.0.1 cannot break if
       the console's address changes under us. */
    char uri[256];
    snprintf(uri, sizeof(uri), "http://127.0.0.1:%d/pkgfile/%d.pkg", (int)PORT, tok);
    const char *label = title[0] ? title : local;
    OrbisBgftTaskId task = BGFT_INVALID_TASK_ID;
    if (bgft_install_url(uri, label, cid, size, pkg_type_for_category(cat), err, errsz, &task) != 0)
        return -6;

    pthread_mutex_lock(&g_job_lock);
    memset(&g_job, 0, sizeof(g_job));
    g_job.active = 1;
    g_job.task = task;
    g_job.job_id = now_ms();
    g_job.started_ms = now_ms();
    g_job.expect = size;
    snprintf(g_job.uri, sizeof(g_job.uri), "%s", uri);
    snprintf(g_job.name, sizeof(g_job.name), "%s", label);
    snprintf(g_job.cid, sizeof(g_job.cid), "%s", cid);
    tid_from_cid(cid, g_job.tid, sizeof(g_job.tid));
    snprintf(g_job.state, sizeof(g_job.state), "downloading");
    snprintf(g_job.msg, sizeof(g_job.msg), "The PS4 is installing it from its own storage");
    pthread_mutex_unlock(&g_job_lock);
    ilog("install: local package %s (%s, %s, %lld bytes) -> task %d",
         local, cid, pkg_type_for_category(cat), size, (int)task);
    return 0;
}

static void install_local_path(int fd, const char *local) {
    char err[320] = {0};
    if (install_local_pkg(local, err, sizeof(err)) != 0) {
        int busy = (strstr(err, "already running") != NULL);
        char esc[400], out[620];
        json_escape(err, esc, sizeof(esc));
        snprintf(out, sizeof(out), "{\"ok\":false,\"queued\":false,%s\"error\":\"%s\"}",
                 busy ? "\"busy\":true," : "", esc);
        if (busy) send_status(fd, "409 Conflict", "application/json", out);
        else      send_json(fd, out);
        return;
    }
    pthread_mutex_lock(&g_job_lock);
    long long jid = g_job.job_id;
    int task = (int)g_job.task;
    pthread_mutex_unlock(&g_job_lock);
    char out[260];
    snprintf(out, sizeof(out),
             "{\"ok\":true,\"queued\":true,\"local\":true,\"job_id\":%lld,\"task\":%d}",
             jid, task);
    send_json(fd, out);
}

#ifndef PMS_LITE
/* ------------------------------------------------ the dashboard app this ELF carries
 *
 * The PS5 build installs its tile on boot; this does the same for the PS4, and for the same reason:
 * without it there is nothing on the console to press, and the shop only exists while this payload
 * happens to be loaded.
 *
 * INTELLIGENTLY, meaning it does the least it can get away with:
 *   - already installed at this version or newer  -> nothing at all, not even a write to disk
 *   - installed but older                         -> install over it, which is how the PS4 updates
 *   - not there                                   -> install it
 *
 * "Installed" is app.pkg on disk with bytes in it, never an app.db row on its own - the rule this
 * project learned on the PS5, where trusting the row produced 53 phantom installs. The version
 * comes from tbl_appinfo's APP_VER, which is the field the console itself updates when a package
 * is installed over another.
 *
 * Nothing here touches the jailbreak's folders or files. The staged copy lives under our own
 * /data/pkg-mutant-shop, and the install goes down our own BGFT lane - the same one a game from
 * the PC uses, serving the file to the console from this very process.
 */
#define PS4_TILE_VER   "01.00"       /* bump when ps4-app/tile-pkg changes; compared numerically */
#define TILE_PKG_DISK  SHOP_DATA_DIR "/pms-tile.pkg"

/* "01.02" -> 102, "1.00" -> 100. Format-tolerant on purpose: the console stores whatever the
   package's param.sfo carried, and a leading zero must not make 01.00 look older than 1.00. */
static int tile_ver_num(const char *v) {
    int n = 0, seen = 0;
    for (const char *p = v ? v : ""; *p; p++) {
        if (*p >= '0' && *p <= '9') { n = n * 10 + (*p - '0'); seen = 1; }
        else if (*p != '.') break;
    }
    return seen ? n : -1;
}

/* The version of the app the console currently has, or "" if it has none. */
static void tile_installed_ver(char *out, size_t outsz) {
    out[0] = 0;
    static ps4_title_t rows[MAX_TITLES];
    pthread_mutex_lock(&g_scan_lock);
    int n = read_console_titles(rows, MAX_TITLES);
    for (int i = 0; i < n; i++)
        if (!strcmp(rows[i].tid, PS4_TILE_TID)) { snprintf(out, outsz, "%s", rows[i].ver); break; }
    pthread_mutex_unlock(&g_scan_lock);
}

/* Write the embedded package out and hand it to the install lane. 0 = handed over. */
static int tile_stage_and_install(char *detail, size_t dsz) {
    size_t len = (size_t)(tb_ps4_tile_pkg_end - tb_ps4_tile_pkg);
    /* A PS4 package begins \x7FCNT. Checking it here means a truncated or mis-built bundle is
       caught before the console is asked to install anything. */
    if (len < 4096 || tb_ps4_tile_pkg[0] != 0x7F || tb_ps4_tile_pkg[1] != 'C' ||
        tb_ps4_tile_pkg[2] != 'N' || tb_ps4_tile_pkg[3] != 'T') {
        snprintf(detail, dsz, "the embedded package looks wrong (%zu bytes)", len);
        return -1;
    }
    mkdir(SHOP_DATA_DIR, 0777);
    int f = open(TILE_PKG_DISK, O_WRONLY | O_CREAT | O_TRUNC, 0777);
    if (f < 0) { snprintf(detail, dsz, "cannot write %s", TILE_PKG_DISK); return -2; }
    size_t off = 0;
    while (off < len) {
        ssize_t w = write(f, tb_ps4_tile_pkg + off, len - off);
        if (w <= 0) break;
        off += (size_t)w;
    }
    close(f);
    if (off != len) { snprintf(detail, dsz, "short write %zu/%zu", off, len); return -3; }

    char err[256] = {0};
    if (install_local_pkg(TILE_PKG_DISK, err, sizeof(err)) != 0) {
        snprintf(detail, dsz, "%s", err);
        return -4;
    }
    snprintf(detail, dsz, "handed over (%zu bytes)", len);
    return 0;
}

/* Decide, then act. Runs on its own thread once the server is listening, because the install lane
   serves the package to the console from this very process - the socket has to be up first. */
static void *tile_thread(void *unused) {
    (void)unused;
    char have[16] = {0};
    long long bytes = installed_app_pkg(PS4_TILE_TID);
    tile_installed_ver(have, sizeof(have));
    int hv = tile_ver_num(have), wv = tile_ver_num(PS4_TILE_VER);

    if (bytes > 0 && hv >= wv) {
        ilog("tile: already installed at %s (carrying %s) - nothing to do", have, PS4_TILE_VER);
        return NULL;
    }
    if (bytes > 0)
        ilog("tile: installed at %s, this build carries %s - updating", have[0] ? have : "?",
             PS4_TILE_VER);
    else
        ilog("tile: not installed - installing %s", PS4_TILE_VER);

    char detail[300] = {0};
    int rc = tile_stage_and_install(detail, sizeof(detail));
    if (rc != 0) {
        ilog("tile: could not install it - %s", detail);
        return NULL;
    }
    /* Wait for the console to finish, then check the FILE rather than the return code. */
    for (int i = 0; i < 90; i++) {
        sleep(2);
        if (installed_app_pkg(PS4_TILE_TID) > 0) {
            ilog("tile: installed (%lld bytes on disk)", installed_app_pkg(PS4_TILE_TID));
            notify("PKG MUTANT SHOP is on your home screen\nOpen it from there any time");
            return NULL;
        }
        job_refresh();
        pthread_mutex_lock(&g_job_lock);
        int failed = g_job.active && !strcmp(g_job.state, "error");
        char why[200];
        snprintf(why, sizeof(why), "%s", g_job.msg);
        pthread_mutex_unlock(&g_job_lock);
        if (failed) { ilog("tile: the console stopped the install - %s", why); return NULL; }
    }
    ilog("tile: the console never finished installing it");
    return NULL;
}

static void tile_start(void) {
    pthread_t t;
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, 256 * 1024);
    if (pthread_create(&t, &at, tile_thread, NULL) == 0) pthread_detach(t);
    pthread_attr_destroy(&at);
}
#else
/* The LITE build is the payload that travels inside the home-screen app's package, so it
   carries no package of its own - that would be a package containing itself. Everything
   else about it is identical, which is the point: pressing the icon starts the same shop. */
static void tile_start(void) { ilog("tile: lite build - no package to install"); }
#endif

static void serve_static(int fd, const char *path) {
    if (strstr(path, "..")) { send_status(fd, "403 Forbidden", "text/plain", "no"); return; }
    char full[700];
    if (!strcmp(path, "/") || path[0] == 0)
        snprintf(full, sizeof(full), "%s/index.html", WEB_ROOT);
    else
        snprintf(full, sizeof(full), "%s%s", WEB_ROOT, path);
    if (send_file(fd, full) != 0)
        send_status(fd, "404 Not Found", "text/plain", "not found");
}

static void handle_get(int fd, const char *rawpath) {
    char path[1024];
    snprintf(path, sizeof(path), "%s", rawpath);
    char *q = strchr(path, '?');
    if (q) *q = 0;

    if (!strcmp(path, "/api/health")) {
        char out[700];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"on_console\":true,\"server\":\"on-console\",\"platform\":\"ps4\","
                 "\"connected\":true,\"version\":\"%s\",\"built\":\"%s %s\",\"ps5_ip\":\"%s\","
                 "\"lan_ip\":\"%s\",\"companion_port\":%d,\"shop_port\":%d,"
                 "\"engine\":\"pms-bgft\",\"engine_ready\":%s,\"ftp_online\":true,\"ftp_port\":2121,"
                 "\"shadowmount\":false,\"shadowmount_port\":0,\"running_title\":\"\"}",
                 SHOP_VERSION, __DATE__, __TIME__, lan_ip_str(), lan_ip_str(), PORT, PORT,
                 g_bgft_ready ? "true" : "false");
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/engine/state")) {
        char out[700];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"platform\":\"ps4\",\"mode\":\"bgft\",\"ours\":true,"
                 "\"name\":\"PKG MUTANT SHOP engine\",\"shop_port\":%d,\"shop_ok\":true,"
                 "\"shop_version\":\"%s\",\"ready\":%s,\"busy\":%s,\"busy_for\":0,"
                 "\"console_ip\":\"%s\",\"shadowmount\":false,\"ftp_port\":2121,"
                 "\"state\":\"%s\",\"detail\":\"%s\",\"how\":\"%s\"}",
                 PORT, SHOP_VERSION, g_bgft_ready ? "true" : "false",
                 g_job.active ? "true" : "false", lan_ip_str(),
                 g_bgft_ready ? (g_job.active ? "busy" : "ready") : "shop-down",
                 g_bgft_ready ? (g_job.active ? "An install is running on the console right now."
                                              : "Ready - installs can start straight away.")
                              : "The background transfer service did not start on this PS4.",
                 "The PS4 downloads and installs the package itself, straight from this PC.");
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/library")) {
        char *j = build_library_json();
        if (j) { send_json(fd, j); free(j); }
        else send_json(fd, "{\"ok\":false,\"error\":\"out of memory\"}");
        return;
    }
    if (!strcmp(path, "/api/installed")) {
        char *j = build_installed_json();
        if (j) { send_json(fd, j); free(j); }
        else send_json(fd, "{\"ok\":false,\"error\":\"out of memory\"}");
        return;
    }
    if (!strcmp(path, "/api/devices"))  { send_devices(fd); return; }
    if (!strcmp(path, "/api/storage"))  { send_storage(fd); return; }

    if (!strncmp(path, "/icon/", 6) || !strncmp(path, "/api/icon/", 10)) {
        const char *t = path + (path[1] == 'i' ? 6 : 10);
        char tid[16];
        snprintf(tid, sizeof(tid), "%.9s", t);
        char ip[600];
        if (icon_path_for(tid, ip, sizeof(ip)) && send_file(fd, ip) == 0) return;
        send_status(fd, "404 Not Found", "text/plain", "no icon");
        return;
    }

    /* ---- the install lane, named exactly as the PS5 build names it ---- */
    if (!strcmp(path, "/api/engine/install-spawn") || !strcmp(path, "/api/engine/install-url")) {
        char uri[1024] = {0}, name[240] = {0};
        if (!qparam(rawpath, "uri", uri, sizeof(uri)) || !uri[0]) {
            send_json(fd, "{\"ok\":false,\"error\":\"no package was named\"}");
            return;
        }
        qparam(rawpath, "name", name, sizeof(name));
        char cid[80] = {0}, szs[32] = {0}, ptype[24] = {0};
        qparam(rawpath, "cid", cid, sizeof(cid));
        qparam(rawpath, "size", szs, sizeof(szs));
        qparam(rawpath, "type", ptype, sizeof(ptype));
        long long psize = szs[0] ? atoll(szs) : 0;
        /* Ask the console how the last job actually ended before deciding we are busy. The state
           in g_job is only as fresh as the last refresh, and nothing refreshes it unless someone
           polls /api/engine/job - so a finished or failed install looked "still running" to the
           very next request and the shop refused it. */
        job_refresh();
        pthread_mutex_lock(&g_job_lock);
        int busy = g_job.active && strcmp(g_job.state, "installed") && strcmp(g_job.state, "error");
        pthread_mutex_unlock(&g_job_lock);
        if (busy) {
            send_status(fd, "409 Conflict", "application/json",
                        "{\"ok\":false,\"busy\":true,\"error\":\"An install is already running on this PS4\"}");
            return;
        }
        char err[256] = {0};
        OrbisBgftTaskId task = BGFT_INVALID_TASK_ID;
        int rc = bgft_install_url(uri, name, cid, psize, ptype, err, sizeof(err), &task);
        if (rc != 0) {
            char esc[300], out[600];
            json_escape(err, esc, sizeof(esc));
            snprintf(out, sizeof(out), "{\"ok\":false,\"queued\":false,\"error\":\"%s\"}", esc);
            send_json(fd, out);
            return;
        }
        pthread_mutex_lock(&g_job_lock);
        memset(&g_job, 0, sizeof(g_job));
        g_job.active = 1;
        g_job.task = task;
        g_job.job_id = now_ms();
        g_job.started_ms = now_ms();
        g_job.expect = psize;
        snprintf(g_job.uri, sizeof(g_job.uri), "%s", uri);
        snprintf(g_job.name, sizeof(g_job.name), "%s", name);
        /* The title id comes out of the CONTENT id first, by STRUCTURE - everything between the
           first '-' and the '_' - because that works for any title id there is. The older reader
           below only recognises a game's (CUSA/NPXS), and this shop installs one package that is
           not a game: its own dashboard app, PKGM00001. That install ran to completion on the
           console and the job never said so, because the title id it was looking for was "". Fall
           back to the game-shaped search for a request that carries no content id at all. */
        tid_from_cid(cid, g_job.tid, sizeof(g_job.tid));
        if (!g_job.tid[0])
            if (!pkg_content_id_from_url_name(name, g_job.tid, sizeof(g_job.tid)))
                pkg_content_id_from_url_name(uri, g_job.tid, sizeof(g_job.tid));
        snprintf(g_job.state, sizeof(g_job.state), "downloading");
        snprintf(g_job.msg, sizeof(g_job.msg), "The PS4 is downloading and installing it");
        long long jid = g_job.job_id;
        pthread_mutex_unlock(&g_job_lock);

        notifyf("%s is installing\nThe PS4 is downloading it now - watch your home screen",
                name[0] ? name : "Your game");
        char out[420];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"queued\":true,\"job_id\":%lld,\"task\":%d,\"rc\":\"0x00000000\"}",
                 jid, (int)task);
        send_json(fd, out);
        return;
    }
    /* INSTALLING SOMETHING THE CONSOLE ALREADY HAS - a stick, or a package copied to the drive.
       It goes through exactly the same installer as a package from the PC: the file is registered,
       served back to the console over loopback, and that URL is handed to BGFT. No separate lane, no
       second verdict path, and nothing that writes a title without its data behind it. */
    /* ROUTES THE PS5 ANSWERS AND THIS CONSOLE CANNOT, answered in words.
     *
     * Unknown /api/ paths fall through to a bare {} so an older page keeps working, and that is
     * exactly what makes a missing route dangerous: the caller reads {} as "nothing to report"
     * rather than "this console does not do that". Each of these is a real button somewhere, so
     * each says what is true instead of going quiet. The POST side of cheats and mods already did
     * this; the GET side did not, which is how /api/cheat/paths answered {} to the settings panel.
     */
    if (!strncmp(path, "/api/cheat", 10) || !strncmp(path, "/api/mods", 9) ||
        !strncmp(path, "/api/patch", 10)) {
        send_json(fd, "{\"ok\":false,\"unsupported\":true,\"platform\":\"ps4\","
                      "\"error\":\"Mods and cheats are not available on the PS4 yet\"}");
        return;
    }
    if (!strcmp(path, "/api/move") || !strcmp(path, "/api/move/status") ||
        !strcmp(path, "/api/game/delete-backup") || !strcmp(path, "/api/game/delete")) {
        /* These act on a mounted backup container, which is a PS5 arrangement. A PS4 game is
           installed or it is not there, so there is nothing here to move or unmount. */
        send_json(fd, "{\"ok\":false,\"unsupported\":true,\"platform\":\"ps4\","
                      "\"error\":\"Game backups are a PS5 feature - a PS4 game is installed "
                      "or it is not there\"}");
        return;
    }
    if (!strcmp(path, "/api/payloads/autostart")) {
        send_json(fd, "{\"ok\":false,\"unsupported\":true,\"platform\":\"ps4\","
                      "\"error\":\"Starting homebrew automatically is a PS5 feature\"}");
        return;
    }
    if (!strcmp(path, "/api/rest/prepare")) {
        /* On the PS5 this stops the payloads that crash the console on wake. The PS4 shop starts
           nothing and manages nothing, so there is genuinely nothing to stop - but the second
           sentence is worth saying, because it was measured: after a suspend and resume this
           console came back with the shop gone. */
        send_json(fd, "{\"ok\":true,\"platform\":\"ps4\",\"stopped\":[],\"survived\":[],"
                      "\"message\":\"Nothing of ours has to be stopped on the PS4 before rest "
                      "mode. The shop has to be loaded again after the console wakes.\"}");
        return;
    }
    if (!strcmp(path, "/api/tile/status")) {
        /* Is the shop on the console's home screen? The PS5 build answers the same question, and
           the page and the companion both ask it - a PS4 that could not answer would simply look
           like a console with no app, which is the very thing this is here to report.

           Proof is the app's own app.pkg, exactly as for a game: a title can be registered in
           app.db and have nothing behind it, and that is the state that reads as "installed" while
           being unlaunchable. THE PACKAGE IS NOT INSTALLED FROM HERE: it carries this payload, so a
           payload that carried it would contain a copy of itself and grow with every build. The PC
           installs it, down the ordinary install lane. */
        long long onDisk = installed_app_pkg(PS4_TILE_TID);
        char out[220];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"platform\":\"ps4\",\"title_id\":\"%s\",\"registered\":%s,"
                 "\"bytes\":%lld,\"installed_by\":\"companion\"}",
                 PS4_TILE_TID, onDisk > 0 ? "true" : "false", onDisk);
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/install/status")) {
        /* THE SAME SHAPE THE PS5 ANSWERS, because the companion's console-local lane polls this and
           nothing else. Without it that lane would have handed the package over, polled a route
           that does not exist, read {} as "not finished yet" and sat there for its full hour before
           reporting that the console never said anything - with the game installed the whole time.
           A route that exists on one payload and not the other is a silent dead button.

           `accepted_only` is false on purpose: unlike the PS5's path, this verdict is not "the
           installer took it", it is app.pkg on disk at the expected size, which is the proof the
           companion would otherwise go and gather for itself. */
        job_refresh();
        pthread_mutex_lock(&g_job_lock);
        int done = g_job.active && (!strcmp(g_job.state, "installed") || !strcmp(g_job.state, "error"));
        int act  = g_job.active && !strcmp(g_job.state, "downloading");
        int okf  = g_job.active && !strcmp(g_job.state, "installed");
        char en[400], em[500], o[1100];
        json_escape(g_job.name, en, sizeof(en));
        json_escape(g_job.msg, em, sizeof(em));
        pthread_mutex_unlock(&g_job_lock);
        snprintf(o, sizeof(o),
                 "{\"active\":%s,\"done\":%s,\"ok\":%s,\"accepted_only\":false,"
                 "\"name\":\"%s\",\"message\":\"%s\"}",
                 act ? "true" : "false", done ? "true" : "false", okf ? "true" : "false", en, em);
        send_json(fd, o);
        return;
    }
    if (!strcmp(path, "/api/engine/install-local")) {
        char lp[1024] = {0};
        if (!qparam(rawpath, "path", lp, sizeof(lp)) || lp[0] != '/') {
            send_json(fd, "{\"ok\":false,\"error\":\"no package was named\"}");
            return;
        }
        install_local_path(fd, lp);
        return;
    }
    if (!strcmp(path, "/api/engine/job")) {
        job_refresh();
        pthread_mutex_lock(&g_job_lock);
        char en[400], em[400];
        json_escape(g_job.name, en, sizeof(en));
        json_escape(g_job.msg, em, sizeof(em));
        char out[1200];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"active\":%s,\"job_id\":%lld,\"task\":%d,\"title_id\":\"%s\","
                 "\"name\":\"%s\",\"done\":%lld,\"total\":%lld,\"state\":\"%s\",\"msg\":\"%s\","
                 "\"rc\":\"0x%08X\"}",
                 g_job.active ? "true" : "false", g_job.job_id, (int)g_job.task, g_job.tid,
                 en, g_job.done, g_job.total, g_job.state, em, g_job.rc);
        pthread_mutex_unlock(&g_job_lock);
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/engine/spawn-status")) {
        job_refresh();
        pthread_mutex_lock(&g_job_lock);
        int running = g_job.active && !strcmp(g_job.state, "downloading");
        int has_result = g_job.active && (!strcmp(g_job.state, "installed") || !strcmp(g_job.state, "error"));
        long long secs = g_job.started_ms ? (now_ms() - g_job.started_ms) / 1000 : 0;
        pthread_mutex_unlock(&g_job_lock);
        char out[260];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"busy\":%s,\"stale\":false,\"busy_for\":%lld,\"has_result\":%s}",
                 running ? "true" : "false", secs, has_result ? "true" : "false");
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/engine/spawn-result")) {
        job_refresh();
        pthread_mutex_lock(&g_job_lock);
        int done = g_job.active && (!strcmp(g_job.state, "installed") || !strcmp(g_job.state, "error"));
        int ok = g_job.active && !strcmp(g_job.state, "installed");
        char em[400];
        json_escape(g_job.msg, em, sizeof(em));
        char out[900];
        /* THE VERDICT IS ON THE HAND-OVER, NOT ON THE WHOLE INSTALL - the same contract the PS5
           build answers here, and the companion is built around it: it polls this for ninety
           seconds and then stops, while the queue that follows allows thirty-five minutes to prove
           the finish from the console's own files. Answering only when the download had completed
           made every install longer than ninety seconds - which is most of them - report "the
           console took this package but never reported back" while it was installing perfectly.
           `rc` is what the companion waits for, so a running job carries the rc the register and
           start calls actually returned: zero, because they succeeded. */
        if (!done && !g_job.active)
            snprintf(out, sizeof(out), "{\"ok\":false,\"pending\":true,\"state\":\"idle\"}");
        else if (!done)
            snprintf(out, sizeof(out),
                     "{\"ok\":true,\"rc\":\"0x00000000\",\"accepted\":true,\"state\":\"%s\","
                     "\"content_id\":\"%s\",\"uri\":\"%s\",\"via\":\"bgft\"}",
                     g_job.state, g_job.tid, g_job.uri);
        else
            snprintf(out, sizeof(out),
                     "{\"ok\":%s,\"rc\":\"0x%08X\",\"content_id\":\"%s\",\"uri\":\"%s\","
                     "\"via\":\"bgft\",\"msg\":\"%s\"}",
                     ok ? "true" : "false", g_job.rc, g_job.tid, g_job.uri, em);
        pthread_mutex_unlock(&g_job_lock);
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/engine/spawn-cleanup")) {
        job_refresh();                 /* same reason as install-spawn: do not read a stale state */
        pthread_mutex_lock(&g_job_lock);
        int done = g_job.active && (!strcmp(g_job.state, "installed") || !strcmp(g_job.state, "error"));
        OrbisBgftTaskId live = (g_job.active && !done) ? g_job.task : BGFT_INVALID_TASK_ID;
        if (done) g_job.active = 0;
        pthread_mutex_unlock(&g_job_lock);
        /* The companion calls this between installs. Sweeping here is what clears tasks left by a
           payload reload or a crash, which no longer have a job to finish them. */
        int freed = bgft_sweep_ours(live);
        char out[96];
        snprintf(out, sizeof(out), "{\"ok\":true,\"cleaned\":true,\"freed\":%d}", freed);
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/engine/cancel")) {
        pthread_mutex_lock(&g_job_lock);
        OrbisBgftTaskId t = g_job.task;
        int act = g_job.active;
        pthread_mutex_unlock(&g_job_lock);
        int rc = -1;
        if (act && bgft_stop_fn) rc = bgft_stop_fn(t);
        if (act) bgft_release(t);
        pthread_mutex_lock(&g_job_lock);
        if (act) { g_job.released = 1;
                   snprintf(g_job.state, sizeof(g_job.state), "error");
                   snprintf(g_job.msg, sizeof(g_job.msg), "Stopped from the shop"); }
        pthread_mutex_unlock(&g_job_lock);
        char out[160];
        snprintf(out, sizeof(out), "{\"ok\":%s,\"stopped\":%s}",
                 act ? "true" : "false", (rc == 0) ? "true" : "false");
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/engine/log")) {
        long len = 0;
        char *log = slurp(INSTALL_LOG, &len);
        if (!log) { send_status(fd, "200 OK", "text/plain", ""); return; }
        const char *tail = log;
        if (len > 60000) tail = log + (len - 60000);
        send_status(fd, "200 OK", "text/plain", tail);
        free(log);
        return;
    }

    /* ---- file API ---- */
    if (!strncmp(path, "/api/fs/", 8)) {
        char p[1024];
        if (!fs_param_path(rawpath, p, sizeof(p))) {
            send_status(fd, "400 Bad Request", "application/json",
                        "{\"ok\":false,\"error\":\"absolute ?path= required\"}");
            return;
        }
        const char *op = path + 8;
        if (!strncmp(op, "stat", 4)) { fs_send_stat(fd, p); return; }
        if (!strncmp(op, "list", 4)) { fs_send_list(fd, p); return; }
        if (!strncmp(op, "read", 4)) { fs_send_read(fd, p); return; }
        if (!strncmp(op, "mkdir", 5)) {
            char tmp[1100];
            snprintf(tmp, sizeof(tmp), "%s/", p);
            mkparents(tmp);
            mkdir(p, 0777);
            struct stat st;
            send_json(fd, (stat(p, &st) == 0 && S_ISDIR(st.st_mode))
                          ? "{\"ok\":true}" : "{\"ok\":false,\"error\":\"could not create it\"}");
            return;
        }
        send_json(fd, "{\"ok\":false,\"error\":\"unknown file operation\"}");
        return;
    }

    /* ---- peers / identity ---- */
    if (!strcmp(path, "/api/register-pc")) {
        char ip[24] = {0}, port[8] = {0}, name[64] = {0}, ver[16] = {0};
        qparam(rawpath, "ip", ip, sizeof(ip));
        qparam(rawpath, "port", port, sizeof(port));
        qparam(rawpath, "name", name, sizeof(name));
        qparam(rawpath, "version", ver, sizeof(ver));
        if (ip[0]) pc_register(ip, port[0] ? atoi(port) : 8710, name, ver);
        send_json(fd, "{\"ok\":true}");
        return;
    }
    if (!strcmp(path, "/api/companion") || !strcmp(path, "/api/pcs")) {
        char out[1400];
        size_t len = 0;
        len += (size_t)snprintf(out + len, sizeof(out) - len, "{\"urls\":[");
        pthread_mutex_lock(&g_pcs_lock);
        int wrote = 0;
        char best[64] = {0};
        for (int i = 0; i < PC_MAX; i++) {
            if (!g_pcs[i].ip[0]) continue;
            char u[80];
            snprintf(u, sizeof(u), "http://%s:%d", g_pcs[i].ip, g_pcs[i].port);
            if (!best[0]) snprintf(best, sizeof(best), "%s", u);
            if (len + 120 >= sizeof(out)) break;
            len += (size_t)snprintf(out + len, sizeof(out) - len, "%s\"%s\"", wrote ? "," : "", u);
            wrote++;
        }
        pthread_mutex_unlock(&g_pcs_lock);
        snprintf(out + len, sizeof(out) - len, "],\"url\":\"%s\"}", best);
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/open")) {
        /* Open the shop on the PS4's OWN screen. This is how the app reaches the television without
           a dashboard tile: one tap in the app here, or on a phone, and the console's browser opens
           the shop. ?url= overrides, which is how a companion points the console at its library.

           FOUR ARGUMENTS, and that is deliberate. The PS5 build calls this same export with four,
           proven on hardware; the published PS4 declaration takes two. Extra arguments in registers
           are harmless on this ABI, whereas passing too few would leave whatever was already in the
           remaining registers to be read as parameters - so four zeros is the form that is correct
           whichever of the two this firmware really has. */
        char url[300] = {0};
        if (!qparam(rawpath, "url", url, sizeof(url)) || !url[0])
            snprintf(url, sizeof(url), "http://%s:%d/", lan_ip_str(), (int)PORT);
        int (*launch)(const char *, int, int, int) =
            (int (*)(const char *, int, int, int))dlsym_any("sceSystemServiceLaunchWebBrowser");
        int lrc = launch ? launch(url, 0, 0, 0) : -1;
        /* The message is not a consolation prize - it carries the address, so a browser that refuses
           to open still leaves the user something they can type on a phone. */
        if (launch && lrc == 0)
            notifyf("Opening PKG MUTANT SHOP\n%s", url);
        else
            notifyf("PKG MUTANT SHOP is running\nOpen %s in any browser", url);
        char eu[340], o[560];
        json_escape(url, eu, sizeof(eu));
        snprintf(o, sizeof(o),
                 "{\"ok\":true,\"launched\":%s,\"notified\":true,\"available\":%s,"
                 "\"url\":\"%s\"}",
                 (launch && lrc == 0) ? "true" : "false", launch ? "true" : "false", eu);
        send_json(fd, o);
        return;
    }
    if (!strcmp(path, "/api/network")) {
        char out[900];
        snprintf(out, sizeof(out),
                 "{\"ok\":true,\"console\":{\"ip\":\"%s\",\"name\":\"PS4\",\"online\":true,"
                 "\"platform\":\"ps4\"},\"peers\":[],\"this_pc\":null}", lan_ip_str());
        send_json(fd, out);
        return;
    }
    if (!strcmp(path, "/api/consoles")) {
        char out[300];
        snprintf(out, sizeof(out),
                 "{\"consoles\":[{\"id\":\"ps4\",\"name\":\"PS4\",\"ip\":\"%s\","
                 "\"platform\":\"ps4\",\"online\":true}]}", lan_ip_str());
        send_json(fd, out);
        return;
    }

    if (!strcmp(path, "/api/notify")) {
        char text[1200] = {0};
        if (qparam(rawpath, "text", text, sizeof(text)) && text[0]) notify(text);
        send_json(fd, "{\"ok\":true}");
        return;
    }
    if (!strcmp(path, "/api/quit")) {
        send_json(fd, "{\"ok\":true,\"bye\":true}");
        close(fd);
        ilog("quit: handing :%d over", PORT);
        /* NEVER _exit() HERE. The PS5 build can, because its payload owns its process. GoldHEN
           injects us INTO A SHARED HOST PROCESS (ScePartyDaemon), so _exit() tears down that whole
           process - including the newly loaded copy of ourselves that just asked us to stand down.
           Measured: the new build asked, the old build exited, and both disappeared, leaving :8710
           dead and the console with no shop at all.
           Instead: stop the accept loop and close the listening socket, which is all the new
           instance needs in order to bind. Our threads end; the host process is untouched. */
        g_quit = 1;
        int s = g_srv;
        g_srv = -1;
        if (s >= 0) { shutdown(s, SHUT_RDWR); close(s); }
        return;
    }

    /* The cheat/patch engine writes another process's memory through the kernel. Under GoldHEN we
       have no kernel access at all, so rather than answer an empty stub that looks like a broken
       button, these say what is true. */
    if (!strncmp(path, "/api/cheat", 10) || !strncmp(path, "/api/mods", 9) ||
        !strncmp(path, "/api/patch", 10) || !strncmp(path, "/api/mem", 8)) {
        send_json(fd, "{\"ok\":false,\"unsupported\":true,"
                      "\"error\":\"Mods and cheats are not available on the PS4 yet\"}");
        return;
    }

    if (!strcmp(path, "/") || path[0] == 0 || strncmp(path, "/api/", 5)) {
        serve_static(fd, path);
        return;
    }
    /* Unknown /api/ stays a quiet 200 {} - the shared page probes routes that only one console
       has, and an error here would paint a failure on a healthy console. */
    send_json(fd, "{}");
}

static void handle_post(int fd, const char *rawpath, const char *body) {
    char path[1024];
    snprintf(path, sizeof(path), "%s", rawpath);
    char *q = strchr(path, '?');
    if (q) *q = 0;

    if (!strcmp(path, "/api/install")) {
        /* The companion normally drives /api/engine/install-spawn; this exists so the on-console
           page can install too, and takes the same {"url": "..."} the PS5 build takes. */
        char uri[1024] = {0};
        const char *u = body ? strstr(body, "\"url\"") : NULL;
        if (u) {
            const char *c = strchr(u, ':');
            if (c) {
                const char *s1 = strchr(c, '"');
                if (s1) {
                    const char *s2 = strchr(s1 + 1, '"');
                    if (s2 && (size_t)(s2 - s1 - 1) < sizeof(uri)) {
                        memcpy(uri, s1 + 1, (size_t)(s2 - s1 - 1));
                        uri[s2 - s1 - 1] = 0;
                    }
                }
            }
        }
        /* The same {"install_key":"local:<path>"} / {"path":"<path>"} the PS5 build takes, so the
           page does not need to know which console it is talking to. */
        if (!uri[0]) {
            char lk[1200] = {0};
            json_str_field(body ? body : "", "install_key", lk, sizeof(lk));
            if (strncmp(lk, "local:", 6))
                json_str_field(body ? body : "", "path", lk, sizeof(lk));
            else
                memmove(lk, lk + 6, strlen(lk + 6) + 1);
            if (lk[0] == '/') { install_local_path(fd, lk); return; }
        }
        if (!uri[0]) { send_json(fd, "{\"ok\":false,\"error\":\"no package was named\"}"); return; }
        char err[256] = {0};
        OrbisBgftTaskId task = BGFT_INVALID_TASK_ID;
        if (bgft_install_url(uri, "", "", 0, "", err, sizeof(err), &task) != 0) {
            char esc[300], out[500];
            json_escape(err, esc, sizeof(esc));
            snprintf(out, sizeof(out), "{\"ok\":false,\"error\":\"%s\"}", esc);
            send_json(fd, out);
            return;
        }
        pthread_mutex_lock(&g_job_lock);
        memset(&g_job, 0, sizeof(g_job));
        g_job.active = 1; g_job.task = task; g_job.job_id = now_ms(); g_job.started_ms = now_ms();
        snprintf(g_job.uri, sizeof(g_job.uri), "%s", uri);
        pkg_content_id_from_url_name(uri, g_job.tid, sizeof(g_job.tid));
        snprintf(g_job.state, sizeof(g_job.state), "downloading");
        pthread_mutex_unlock(&g_job_lock);
        send_json(fd, "{\"ok\":true,\"queued\":true}");
        return;
    }
    if (!strncmp(path, "/api/cheat", 10) || !strncmp(path, "/api/mods", 9) ||
        !strncmp(path, "/api/patch", 10)) {
        send_json(fd, "{\"ok\":false,\"unsupported\":true,"
                      "\"error\":\"Mods and cheats are not available on the PS4 yet\"}");
        return;
    }
    send_json(fd, "{}");
}

/* ------------------------------------------------------------ self-extraction
   Loading a new payload delivers the current UI with it, exactly like the PS5 build. */

static void extract_web(void) {
    mkdir("/data", 0777);
    mkdir(SHOP_DATA_DIR, 0777);
    mkdir(WEB_ROOT, 0777);
    int wrote = 0;
    for (int i = 0; i < WEB_FILES_COUNT; i++) {
        char full[600];
        snprintf(full, sizeof(full), "%s/%s", WEB_ROOT, WEB_FILES[i].path);
        mkparents(full);
        /* config.js is configuration, not build output: deploy writes the companion URL into it
           and overwriting it on every load would throw that away. */
        if (!strcmp(WEB_FILES[i].path, "config.js")) {
            struct stat cst;
            if (stat(full, &cst) == 0 && cst.st_size > 0) continue;
        }
        int fd = open(full, O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (fd < 0) continue;
        const unsigned char *d = WEB_FILES[i].data;
        unsigned int left = WEB_FILES[i].len;
        while (left > 0) {
            int w = (int)write(fd, d, left);
            if (w <= 0) break;
            d += w; left -= (unsigned)w;
        }
        close(fd);
        wrote++;
    }
    ilog("web: %d file(s) written to %s", wrote, WEB_ROOT);
}

/* ---------------------------------------------------------------------- main */

typedef struct { int fd; } conn_t;

static void *conn_thread(void *arg) {
    conn_t *c = (conn_t *)arg;
    int fd = c->fd;
    free(c);
    struct timeval to;
    to.tv_sec = 30; to.tv_usec = 0;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &to, sizeof(to));
    to.tv_sec = 60;
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &to, sizeof(to));

    char buf[8192];
    int n = (int)read(fd, buf, sizeof(buf) - 1);
    if (n <= 0) { close(fd); return NULL; }
    buf[n] = 0;

    int is_post = !strncmp(buf, "POST ", 5);
    int is_get  = !strncmp(buf, "GET ", 4);
    int is_head = !strncmp(buf, "HEAD ", 5);
    char path[1024] = "/";
    if (is_get || is_post || is_head) {
        const char *s = buf + ((is_post || is_head) ? 5 : 4);
        const char *e = strchr(s, ' ');
        if (e && (size_t)(e - s) < sizeof(path)) { memcpy(path, s, (size_t)(e - s)); path[e - s] = 0; }
    }

    /* The package stream BGFT pulls. Handled here rather than in handle_get because it is the
       only route that needs the request HEADERS - a Range request - and because the send timeout
       set above must come off first: a multi-gigabyte transfer stalls for longer than a minute
       whenever the console pauses to promote what it already has, and a write that times out
       mid-stream ends the install. */
    if ((is_get || is_head) && !strncmp(path, "/pkgfile/", 9)) {
        serve_pkgfile(fd, path, buf, is_head);
        close(fd);
        return NULL;
    }
    if (is_post && !strncmp(path, "/api/fs/write", 13)) {
        char *he = strstr(buf, "\r\n\r\n");
        int hl = he ? (int)(he - buf) + 4 : n;
        fs_recv_write(fd, path, buf, hl, he ? n - hl : 0);
        close(fd);
        return NULL;
    }
    if (is_get || is_head) {
        handle_get(fd, path);
    } else if (is_post) {
        char *hdr_end = strstr(buf, "\r\n\r\n");
        int blen = 0;
        const char *cl_h = strcasestr_local(buf, "content-length:");
        if (cl_h) blen = atoi(cl_h + 15);
        char *body = hdr_end ? hdr_end + 4 : NULL;
        int have = body ? n - (int)(body - buf) : 0;
        if (blen > 0 && blen < (int)sizeof(buf) - 1 && body) {
            while (have < blen) {
                int r = (int)read(fd, body + have, (size_t)(blen - have));
                if (r <= 0) break;
                have += r;
            }
            body[have] = 0;
        }
        handle_post(fd, path, body ? body : "");
    } else {
        send_status(fd, "405 Method Not Allowed", "text/plain", "GET or POST only");
    }
    close(fd);
    return NULL;
}

int main(void) {
    klog_puts("[PMS] PKG MUTANT SHOP for PS4 starting");
    /* A WRITE TO A CLOSED SOCKET MUST NOT KILL US, and on this console that is not a theoretical
       worry: the transfer service opens the package stream, gives up part-way (a task it decides
       to abandon, a retry, a stop) and closes - and the next write raises SIGPIPE, whose default
       action is to terminate. We are injected into a SHARED system process, so "terminate" means
       the shop vanishes mid-install and takes whatever else lives in that process with it.
       Measured: klog logged "A user thread receives a fatal signal" the first time a package was
       served to the console's own downloader. Ignore it and let write() return EPIPE, which every
       loop here already checks. */
    signal(SIGPIPE, SIG_IGN);
    mkdir("/data", 0777);
    mkdir(SHOP_DATA_DIR, 0777);
    ilog("==== BOOT: PKG MUTANT SHOP PS4 %s (%s %s) ====", SHOP_VERSION, __DATE__, __TIME__);

    extract_web();
    bgft_bootstrap();

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) {
        notify("PKG MUTANT SHOP could not start\nThe console would not give it a network socket");
        return 1;
    }
    g_srv = srv;
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(PORT);

    /* If an older build of ourselves holds the port, ask it to stand down and take over - the
       normal way this payload is updated. */
    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        int taken = 0;
        for (int attempt = 0; attempt < 12 && !taken; attempt++) {
            if (attempt == 0) {
                int q = socket(AF_INET, SOCK_STREAM, 0);
                struct sockaddr_in la;
                memset(&la, 0, sizeof(la));
                la.sin_family = AF_INET;
                la.sin_addr.s_addr = inet_addr("127.0.0.1");
                la.sin_port = htons(PORT);
                struct timeval t2; t2.tv_sec = 3; t2.tv_usec = 0;
                setsockopt(q, SOL_SOCKET, SO_RCVTIMEO, &t2, sizeof(t2));
                if (q >= 0 && connect(q, (struct sockaddr *)&la, sizeof(la)) == 0) {
                    const char *rq = "GET /api/quit HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
                    write_all(q, rq, strlen(rq));
                    char tmp[64];
                    ssize_t rr = read(q, tmp, sizeof(tmp));
                    (void)rr;
                }
                if (q >= 0) close(q);
            }
            usleep(400000);
            close(srv);
            srv = socket(AF_INET, SOCK_STREAM, 0);
            if (srv < 0) break;
            g_srv = srv;
            setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
            if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) == 0) taken = 1;
        }
        if (!taken) {
            notify("PKG MUTANT SHOP is already running\nNo need to load it again");
            return 0;
        }
    }
    listen(srv, 16);
    ilog("listening on :%d as %s", PORT, lan_ip_str());
    notifyf("PKG MUTANT SHOP v%s is ready\nOpen %s:%d in any browser", SHOP_VERSION, lan_ip_str(), PORT);

    /* THE DASHBOARD APP, after the socket is listening and not before: the install lane serves the
       package to the console from this very process, so there has to be something to serve it. On
       its own thread - a console that is mid-install must not hold up the shop opening. */
    tile_start();

    while (!g_quit) {
        int cl = accept(srv, 0, 0);
        if (cl < 0) {
            /* accept() failing while g_quit is set IS the handover - the quit handler closed this
               socket on purpose so a newly loaded build can bind. Anything else is transient. */
            if (g_quit) break;
            continue;
        }
        /* One thread per connection: a multi-GB upload or a slow client must never stop the UI
           from answering, which is the trap the PS5 build hit with a single accept loop. */
        conn_t *c = (conn_t *)malloc(sizeof(conn_t));
        if (!c) { close(cl); continue; }
        c->fd = cl;
        pthread_t t;
        pthread_attr_t at;
        pthread_attr_init(&at);
        pthread_attr_setstacksize(&at, 256 * 1024);
        if (pthread_create(&t, &at, conn_thread, c) != 0) { free(c); close(cl); }
        else pthread_detach(t);
        pthread_attr_destroy(&at);
    }
    if (g_srv >= 0) { close(g_srv); g_srv = -1; }
    ilog("stopped serving :%d", PORT);
    return 0;
}
