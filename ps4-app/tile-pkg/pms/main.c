/* PKG MUTANT SHOP - the PS4 dashboard app.
 *
 * WHAT IT IS. The icon on the PS4's home screen. Pressing it opens the shop on the television.
 * That is the whole job, and it is deliberately the whole job: this is the PS4 twin of the PS5's
 * dashboard tile, which is likewise a small thing whose only purpose is to open the shop that the
 * ELF serves. The ELF is the shop; this is the button.
 *
 * WHY IT CARRIES NOTHING. An earlier version shipped the shop's payload inside the package and
 * handed it to a payload loader. That had to go, and the reason is structural rather than taste:
 *
 *     The ELF installs this package, the way the PS5 ELF installs its tile. If the package also
 *     contained the ELF, the ELF would contain a copy of itself - and every rebuild would embed
 *     the previous one, growing without limit.
 *
 * So the package carries an icon and this program and nothing else, and the ELF carries the
 * package. One direction only, which is the shape the PS5 has always had.
 *
 * WHAT THAT MEANS WHEN THE SHOP IS NOT RUNNING. The same thing it means on the PS5: a button
 * cannot conjure the server. It says so in words instead of opening a browser onto a dead port -
 * which is exactly what an icon that "does nothing" looks like from the sofa.
 *
 * NOTHING HERE IS PRIVILEGED. A loopback connect, its own /app0, and two public system-service
 * calls. An application runs sandboxed - it cannot read /data and it cannot inject code into
 * another process - so a button that needed either would be a button that does not work.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <orbis/libkernel.h>
#include <orbis/SystemService.h>
#include <orbis/UserService.h>

#define SHOP_PORT 8710
#define SHOP_URL  "http://127.0.0.1:8710/"

/* The notification the console draws.
 *
 * THE ICON FIELD MUST STAY ZERO. `useIconImageUri = 1` selects the form that draws an icon beside
 * the text, and on this console family that form returns success and renders NOTHING - this project
 * has been caught by it twice already on the PS5. The shop's payload sends the plain form, measured
 * working on this exact console, so this sends the identical thing. An app whose every message is
 * invisible looks broken no matter what it actually did. */
static void notify(const char *text) {
    OrbisNotificationRequest req;
    memset(&req, 0, sizeof(req));
    snprintf(req.message, sizeof(req.message), "%s", text);
    sceKernelSendNotificationRequest(0, &req, sizeof(req), 0);
}

/* Is the shop listening on loopback? A refused connect settles immediately, so this costs nothing. */
static int shop_is_up(void) {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    if (s < 0) return 0;
    struct timeval tv;
    tv.tv_sec = 2; tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    struct sockaddr_in a;
    memset(&a, 0, sizeof(a));
    a.sin_family = AF_INET;
    a.sin_port = htons(SHOP_PORT);
    a.sin_addr.s_addr = inet_addr("127.0.0.1");
    int rc = connect(s, (struct sockaddr *)&a, sizeof(a));
    close(s);
    return rc == 0;
}

/* Opening the browser has a prerequisite the shop's payload does not: the user service. The
 * reference program for this on PS4 - the browser sample in the payload SDK - initialises it first
 * and only launches if that succeeded. A payload inherits a process that has already done it; a
 * sandboxed application has not.
 *
 * FOUR ARGUMENTS, deliberately. The published PS4 declaration takes none (it is a stub), and the
 * PS5 build of this shop calls the same export with four, proven on hardware. Extra arguments in
 * registers are harmless on this ABI; passing too few would leave whatever was already in the
 * remaining registers to be read as parameters. Four is the form that is right either way. */
static void open_browser(const char *url) {
    int rc = sceUserServiceInitialize(0);
    printf("[PMS] sceUserServiceInitialize rc=0x%08x\n", rc);
    sceSystemServiceLaunchWebBrowser(url, 0, 0, 0);
    sceKernelUsleep(3 * 1000000);
    sceUserServiceTerminate();
}

int main(void) {
    setvbuf(stdout, NULL, _IONBF, 0);
    printf("[PMS] PKG MUTANT SHOP app starting\n");

    /* Three tries over about four seconds. The shop is normally already up - it is what installed
       this app - but a console just back from rest can still be settling. */
    int up = 0;
    for (int i = 0; i < 3 && !up; i++) {
        up = shop_is_up();
        if (!up) sceKernelUsleep(1500 * 1000);
    }

    if (up) {
        printf("[PMS] the shop is answering - opening it\n");
        notify("Opening PKG MUTANT SHOP");
        open_browser(SHOP_URL);
        return 0;
    }

    /* The one case a person has to act on, so it says what to do rather than failing silently. It
       does NOT name the jailbreak software: the house style forbids naming software this project
       does not ship - tools/message_report.py --check enforces that and reads this file - and on a
       television the name of a payload loader means nothing to the person reading it. */
    printf("[PMS] nothing is answering on :%d\n", SHOP_PORT);
    notify("PKG MUTANT SHOP is not running on this PS4\n"
           "Load it from the PC app or your jailbreak, then open this again");
    sceKernelUsleep(6 * 1000000);
    return 1;
}
