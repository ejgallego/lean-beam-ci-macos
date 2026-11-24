/* Compile the production POSIX implementation with deterministic short/interrupted syscalls. */
#define _GNU_SOURCE
#define _DARWIN_C_SOURCE
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <unistd.h>

static unsigned reads, writes;
static ssize_t interrupted_read(int fd, void *buf, size_t count) {
  if (++reads == 1) { errno = EINTR; return -1; }
  return read(fd, buf, count);
}
static ssize_t short_write(int fd, const void *buf, size_t count) {
  if (++writes == 1) { errno = EINTR; return -1; }
  return write(fd, buf, count > 7 ? 7 : count);
}
static int deny_signal;
static int signal_group(pid_t pid, int sig) {
  if (deny_signal) { errno = EACCES; return -1; }
  return kill(pid, sig);
}
#define kill signal_group
#define read interrupted_read
#define write short_write
#include "../../Beam/Native/process_job.c"
#undef read
#undef write
#undef kill

int main(void) {
  int ends[2] = {-1, -1};
  assert(make_pipe(ends) == 0);
  uint8_t payload[127], echo[sizeof(payload)];
  for (size_t i = 0; i < sizeof(payload); i++) payload[i] = i;
  assert(write_all(ends[1], payload, sizeof(payload)) == 0 && writes > 2);
  size_t count;
  assert(read_some(ends[0], echo, sizeof(echo), &count) == 0 && reads == 2);
  assert(count == sizeof(payload) && memcmp(echo, payload, count) == 0);
  close(ends[0]);
  signal(SIGPIPE, SIG_DFL);
  assert(write_all(ends[1], payload, 1) == EPIPE);
  close(ends[1]);

  /* No standard fd initially exists. The child must still have three working pipes, a
     new session, inherited environment and the requested cwd. It stays alive for inspection. */
  assert(setenv("BEAM_PIPE_FIXTURE", "inherited", 1) == 0);
  close(0); close(1); close(2);
  char *argv[] = {"/bin/sh", "-c",
    "test \"$BEAM_PIPE_FIXTURE\" = inherited && test . -ef / && "
    "read line && printf '%s' \"$line\" && printf err >&2 && exit 73", NULL};
  owned_process process = {0};
  pipe_end parent[3];
  assert(start_process(&process, argv, "/", parent) == 0);
  assert(getsid(process.pid) == process.pid && getpgid(process.pid) == process.pid);
  for (size_t i = 0; i < 3; i++) assert(parent[i] >= 3 && (fcntl(parent[i], F_GETFD) & FD_CLOEXEC));
  assert(write_all(parent[0], (const uint8_t *)"ok\n", 3) == 0);
  assert(read_some(parent[1], echo, sizeof(echo), &count) == 0 && count == 2 && !memcmp(echo, "ok", 2));
  assert(read_some(parent[2], echo, sizeof(echo), &count) == 0 && count == 3 && !memcmp(echo, "err", 3));
  siginfo_t info;
  assert(waitid(P_PID, process.pid, &info, WEXITED | WNOWAIT) == 0);
  assert(terminate_process(&process) == 0 && collect_process(&process) == 0 && process.code == 73);
  assert(terminate_process(&process) == 0 && collect_process(&process) == 0 && process.code == 73);
  for (size_t i = 0; i < 3; i++) close(parent[i]);

  /* Exercise loss of ownership without ever signalling an arbitrary numeric PID. */
  owned_process lost = {0};
  char *quick[] = {"/bin/sh", "-c", "exit 0", NULL};
  assert(start_process(&lost, quick, NULL, parent) == 0);
  assert(waitpid(lost.pid, NULL, 0) == lost.pid);
  assert(terminate_process(&lost) == ECHILD && lost.lost);
  assert(collect_process(&lost) == ECHILD);
  for (size_t i = 0; i < 3; i++) close(parent[i]);
  owned_process *abandoned = calloc(1, sizeof(*abandoned));
  assert(abandoned && start_process(abandoned, quick, NULL, parent) == 0);
  pid_t pid = abandoned->pid;
  assert(waitid(P_PID, pid, &info, WEXITED | WNOWAIT) == 0);
  deny_signal = 1;
  finalize_process(abandoned);
  assert(waitid(P_PID, pid, &info, WEXITED | WNOWAIT) < 0 && errno == ECHILD);
  for (size_t i = 0; i < 3; i++) close(parent[i]);
  return 0;
}
