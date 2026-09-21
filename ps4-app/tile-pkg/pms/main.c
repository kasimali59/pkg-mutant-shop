/* PKG MUTANT SHOP - the PS4 dashboard app.
 *
 * WHY THIS EXISTS. On the PS5 the shop puts a tile on the dashboard: a small package the ELF
 * installs on boot, whose only job is to open the shop's own page. Without the PS4 equivalent there
 * is nothing on the PS4 to press - the shop only exists while a payload happens to be injected, and
 * the jailbreak's payload loader does not survive rest mode. So a PS4 with the shop "installed" had
 * no way to reach it, which is exactly what a person sees as "the app is not there".
 *
 * WHAT IT DOES, in order:
 *   1. is the shop already answering on 127.0.0.1:8710?  -> just open it
 *   2. if not, hand the payload it carries to the jailbreak's own binary loader on :9090 and wait
 *   3. open the console's browser at the shop
 *   4. if none of that worked, say so on screen in words, with the address to try by hand
 *
 * WHY IT LOADS A PAYLOAD RATHER THAN BEING THE SERVER. An application is suspended the moment the
 * browser comes to the foreground, and a suspended process stops answering its socket - so a shop
 * served by THIS process would die at the exact moment the page tried to load. The payload lives in
 * a long-running system process instead, which is why it survives the browser, the app closing, and
 * everything else. This app is the button; the payload is the shop.
 *
 * NOTHING HERE IS PRIVILEGED. It opens loopback sockets, reads its own /app0, and calls two public
 * system-service functions. That is deliberate: an application runs sandboxed, and a tile that
 * needed more than a sandbox allows would be a tile that does not work.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <orbis/libkernel.h>
#include <orbis/SystemService.h>

#define SHOP_PORT     8710
#define LOADER_PORT   9090
#define PAYLOAD_PATH  "/app0/pms-payload.elf"
#define SHOP_URL      "http://127.0.0.1:8710/"

/* The notification the console draws. OrbisNotificationRequest and the call itself are the
   TOOLCHAIN's own declarations, not a copy made here - the shop's payload had to hand-roll this
   struct because its SDK has no header for it, and two copies of a 3120-byte layout is exactly the
   kind of thing that silently drifts. */
static void notify(const char *text) {
    OrbisNotificationRequest req;
    memset(&req, 0, sizeof(req));
    req.useIconImageUri = 1;
    snprintf(req.message, sizeof(req.message), "%s", text);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

/* Is something listening on loopback? A refused connect settles immediately, so this is cheap. */
static int port_open(int port) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return 0;
    struct timeval tv;
    tv.tv_sec = 2; tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons((unsigned short)port);
    a.sin_addr.s_addr = inet_addr("127.0.0.1");
    int rc = connect(s, (struct sockaddr *)&a, sizeof(a));
    close(s);
    return rc == 0;
}

static int write_all(int fd, const char *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t w = write(fd, buf + off, len - off);
        if (w <= 0) return -1;
        off += (size_t)w;
    }
    return 0;
}

/* Hand our payload to the jailbreak's binary loader, exactly the way the PC does it over the
   network: one HTTP POST carrying the whole ELF. Returns 0 if the loader took it.

   NEVER opens a connection it does not then fill with the payload. A loader that is handed an
   empty connection can stop listening - which is how this console lost its loader twice while this
   was being built, from nothing more than a port scan. */
static int send_payload_to_loader(void) {
    struct stat st;
    if (stat(PAYLOAD_PATH, &st) != 0 || st.st_size <= 0) return -1;

    int f = open(PAYLOAD_PATH, O_RDONLY);
    if (f < 0) return -1;

    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) { close(f); return -1; }
    struct timeval tv;
    tv.tv_sec = 30; tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(LOADER_PORT);
    a.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(s, (struct sockaddr *)&a, sizeof(a)) != 0) { close(s); close(f); return -1; }

    char hdr[256];
    int hn = snprintf(hdr, sizeof(hdr),
                      "POST / HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
                      "Content-Type: application/octet-stream\r\n"
                      "Content-Length: %lld\r\nConnection: close\r\n\r\n",
                      LOADER_PORT, (long long)st.st_size);
    if (write_all(s, hdr, (size_t)hn) != 0) { close(s); close(f); return -1; }

    char *buf = (char *)malloc(64 * 1024);
    if (!buf) { close(s); close(f); return -1; }
    int bad = 0;
    for (;;) {
        ssize_t r = read(f, buf, 64 * 1024);
        if (r < 0) { bad = 1; break; }
        if (r == 0) break;
        if (write_all(s, buf, (size_t)r) != 0) { bad = 1; break; }
    }
    free(buf);
    close(f);
    /* Read whatever it answers so the loader is not left writing into a closed socket. */
    char rep[256];
    if (!bad) (void)read(s, rep, sizeof(rep));
    close(s);
    return bad ? -1 : 0;
}

/* FOUR ARGUMENTS, deliberately. The published PS4 declaration takes none (it is a stub), and the
   PS5 build of this shop calls the same export with four, proven on hardware. Extra arguments in
   registers are harmless on this ABI; passing too few would leave whatever was already in the
   remaining registers to be read as parameters. Four is the form that is right either way. */
static void open_browser(const char *url) {
    sceSystemServiceLaunchWebBrowser(url, 0, 0, 0);
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("[PMS] PKG MUTANT SHOP tile starting\n");

    if (port_open(SHOP_PORT)) {
        printf("[PMS] the shop is already running\n");
        notify("Opening PKG MUTANT SHOP");
        open_browser(SHOP_URL);
        sceKernelUsleep(3 * 1000000);
        return 0;
    }

    printf("[PMS] the shop is not running - handing the payload to the loader\n");
    if (send_payload_to_loader() == 0) {
        /* Give it the same run-up the PC gives it: the payload extracts the web files and opens
           its socket, which takes a few seconds on a cold start. */
        for (int i = 0; i < 30; i++) {
            sceKernelUsleep(1000 * 1000);
            if (port_open(SHOP_PORT)) {
                printf("[PMS] the shop came up after %d second(s)\n", i + 1);
                notify("Opening PKG MUTANT SHOP");
                open_browser(SHOP_URL);
                sceKernelUsleep(3 * 1000000);
                return 0;
            }
        }
        notify("PKG MUTANT SHOP could not start\nThe payload was accepted but the shop did not open");
        printf("[PMS] payload accepted but :8710 never opened\n");
        return 1;
    }

    /* The loader is not there. This is the one case a person has to act on, so it says exactly
       what to do rather than failing silently. */
    notify("PKG MUTANT SHOP cannot start\nRun the jailbreak again, then open this app");
    printf("[PMS] no loader on :%d and no shop on :%d\n", LOADER_PORT, SHOP_PORT);
    sceKernelUsleep(5 * 1000000);
    return 1;
}
