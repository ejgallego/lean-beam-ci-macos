/* Non-shipped syscall reproduction. No Lean, Lake, project code, or external commands. */
#define _POSIX_C_SOURCE 200809L
#if defined(__APPLE__)
#define _DARWIN_C_SOURCE
#endif
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#if defined(__APPLE__)
#include <sys/proc.h>
#include <sys/sysctl.h>
#endif

static void snapshot(pid_t leader, const char *phase) {
  errno = 0;
  pid_t group = getpgid(leader);
  int group_errno = group < 0 ? errno : 0;
  errno = 0;
  pid_t session = getsid(leader);
  int session_errno = session < 0 ? errno : 0;
  printf("{\"event\":\"snapshot\",\"phase\":\"%s\",\"leader\":%d,"
    "\"group\":%d,\"group_errno\":%d,\"session\":%d,\"session_errno\":%d",
    phase, leader, group, group_errno, session, session_errno);
#if defined(__APPLE__)
  int query[] = {CTL_KERN, KERN_PROC, KERN_PROC_PGRP, leader};
  size_t capacity = 0;
  errno = 0;
  int size_rc = sysctl(query, 4, NULL, &capacity, NULL, 0);
  int size_errno = size_rc ? errno : 0;
  printf(",\"size_rc\":%d,\"size_errno\":%d,\"capacity\":%zu,\"record_bytes\":%zu",
    size_rc, size_errno, capacity, sizeof(struct kinfo_proc));
  if (!size_rc && capacity && capacity <= 16 * 1024 * 1024) {
    struct kinfo_proc *members = malloc(capacity);
    if (members) {
      size_t size = capacity;
      errno = 0;
      int read_rc = sysctl(query, 4, members, &size, NULL, 0);
      int read_errno = read_rc ? errno : 0;
      printf(",\"read_rc\":%d,\"read_errno\":%d,\"returned_bytes\":%zu,\"members\":[",
        read_rc, read_errno, size);
      if (!read_rc && size <= capacity && size % sizeof(*members) == 0) {
        for (size_t i = 0; i < size / sizeof(*members); i++) {
          printf("%s{\"pid\":%d,\"ppid\":%d,\"pgid\":%d,\"state\":%d,\"flags\":%u}",
            i ? "," : "", members[i].kp_proc.p_pid, members[i].kp_eproc.e_ppid,
            members[i].kp_eproc.e_pgid, members[i].kp_proc.p_stat,
            (unsigned)members[i].kp_proc.p_flag);
        }
      }
      printf("]");
      free(members);
    } else printf(",\"allocation_failed\":true");
  }
#else
  printf(",\"darwin_sysctl\":false");
#endif
  printf("}\n");
}

static void *idle(void *unused) {
  (void)unused;
  for (;;) pause();
  return NULL;
}

static int observe_exit(pid_t child) {
  siginfo_t status = {0};
  int rc;
  do rc = waitid(P_PID, (id_t)child, &status, WEXITED | WNOWAIT); while (rc && errno == EINTR);
  printf("{\"event\":\"waitid_no_reap\",\"rc\":%d,\"errno\":%d,\"pid\":%d,\"code\":%d}\n",
    rc, rc ? errno : 0, status.si_pid, status.si_code);
  return rc;
}

static int sample(int mode, int group_first, int iteration) {
  int ready[2];
  if (pipe(ready)) return 1;
  pid_t child = fork();
  if (child < 0) { close(ready[0]); close(ready[1]); return 1; }
  if (!child) {
    close(ready[0]);
    if (mode == 3) {
      struct timespec delay = {0, 20000000};
      nanosleep(&delay, NULL);
    }
    if (setsid() < 0) _exit(10);
    if (mode == 1) {
      for (int i = 0; i < 8; i++) {
        pthread_t thread;
        if (pthread_create(&thread, NULL, idle, NULL)) _exit(11);
      }
    }
    if (write(ready[1], "r", 1) != 1) _exit(12);
    close(ready[1]);
    if (mode == 2) _exit(0);
    idle(NULL);
    _exit(13);
  }
  close(ready[1]);
  if (mode != 3) {
    char byte;
    ssize_t count;
    do count = read(ready[0], &byte, 1); while (count < 0 && errno == EINTR);
    if (count != 1) {
      kill(child, SIGKILL);
      close(ready[0]);
      waitpid(child, NULL, 0);
      return 1;
    }
  }
  close(ready[0]);
  printf("{\"event\":\"case\",\"mode\":%d,\"group_first\":%d,\"iteration\":%d,\"leader\":%d}\n",
    mode, group_first, iteration, child);
  if (mode == 2 && observe_exit(child)) return 1;
  snapshot(child, "before_signals");
  int leader_rc, leader_errno, group_rc, group_errno;
  /* Do not print, query, or reap between these two signals: preserve the production race. */
  if (group_first) {
    group_rc = kill(-child, SIGKILL); group_errno = group_rc ? errno : 0;
    leader_rc = kill(child, SIGKILL); leader_errno = leader_rc ? errno : 0;
  } else {
    leader_rc = kill(child, SIGKILL); leader_errno = leader_rc ? errno : 0;
    group_rc = kill(-child, SIGKILL); group_errno = group_rc ? errno : 0;
  }
  printf("{\"event\":\"signals\",\"leader_rc\":%d,\"leader_errno\":%d,"
    "\"group_rc\":%d,\"group_errno\":%d}\n", leader_rc, leader_errno, group_rc, group_errno);
  snapshot(child, "after_signals");
  int failed = observe_exit(child) != 0;
  snapshot(child, "after_observed_exit_no_reap");
  /* A pre-setsid child can establish its group between the first group signal and leader kill.
     Retain the leader until this final group signal, even after observing its exit. */
  int final_rc = kill(-child, SIGKILL);
  int final_errno = final_rc ? errno : 0;
  printf("{\"event\":\"final_group_signal\",\"rc\":%d,\"errno\":%d}\n", final_rc, final_errno);
  int status;
  pid_t reaped;
  do reaped = waitpid(child, &status, 0); while (reaped < 0 && errno == EINTR);
  printf("{\"event\":\"reap\",\"pid\":%d,\"errno\":%d}\n", reaped, reaped < 0 ? errno : 0);
  return failed || reaped != child;
}

int main(void) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  const char *modes[] = {"ready single-threaded", "ready eight threads", "already exited", "before setsid"};
  for (int mode = 0; mode < 4; mode++) {
    printf("{\"event\":\"mode\",\"mode\":%d,\"name\":\"%s\"}\n", mode, modes[mode]);
    for (int order = 0; order < 2; order++)
      for (int iteration = 0; iteration < 8; iteration++)
        if (sample(mode, order, iteration)) return 1;
  }
  return 0;
}
