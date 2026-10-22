/* Test media only: consume an actual key event from QEMU's USB HID keyboard. */
#include <fcntl.h>
#include <linux/input.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
int main(void) {
    for (int i = 0; i < 32; i++) {
        char path[64], name[256] = {0};
        snprintf(path, sizeof(path), "/dev/input/event%d", i);
        int fd = open(path, O_RDONLY);
        if (fd < 0) continue;
        if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) >= 0 && strstr(name, "QEMU USB Keyboard")) {
            puts("ZXS_USB_KEYBOARD_READY");
            fflush(stdout);
            struct input_event event;
            while (read(fd, &event, sizeof(event)) == sizeof(event)) {
                if (event.type == EV_KEY && event.code == KEY_A && event.value == 1) {
                    close(fd);
                    return 0;
                }
            }
        }
        close(fd);
    }
    return 1;
}
