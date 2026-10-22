/* Loopback-only HTTP fixture, downloaded into the disposable QEMU test disk. */
#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void)
{
    int listener = socket(AF_INET, SOCK_STREAM, 0);
    int reuse = 1;
    struct sockaddr_in address = {
        .sin_family = AF_INET,
        .sin_port = htons(8080),
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK),
    };
    const char response[] = "HTTP/1.1 200 OK\r\nContent-Length: 19\r\n"
                            "Connection: close\r\n\r\nzxs-linux-https-ok\n";
    signal(SIGPIPE, SIG_IGN);
    if (listener < 0 || setsockopt(listener, SOL_SOCKET, SO_REUSEADDR,
                                  &reuse, sizeof(reuse)) < 0 ||
        bind(listener, (struct sockaddr *)&address, sizeof(address)) < 0 ||
        listen(listener, 8) < 0) {
        perror("HTTP test fixture");
        return 1;
    }
    for (;;) {
        int client = accept(listener, NULL, NULL);
        char request[4096];
        if (client < 0) {
            perror("accept");
            return 1;
        }
        if (read(client, request, sizeof(request)) > 0) {
            size_t sent = 0;
            while (sent < strlen(response)) {
                ssize_t count = write(client, response + sent, strlen(response) - sent);
                if (count <= 0)
                    break;
                sent += (size_t)count;
            }
        }
        close(client);
    }
}
