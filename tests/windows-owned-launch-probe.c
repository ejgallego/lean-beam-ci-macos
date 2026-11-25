/* Standalone hosted Windows experiment. No Lean or Beam runtime linkage. */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

static WCHAR executable[MAX_PATH * 4];
static PROCESS_INFORMATION owned_child;
static HANDLE owned_job;
static HANDLE safety_job;
#define JOB_MARKER ((DWORD)0xBEA00009u)

typedef struct {
  uint64_t process, descendant;
  DWORD created, assigned, error, failure_stage;
  DWORD stdin_ok, stderr_ok, target_child, target_descendant, sentinel_set;
  DWORD last_running, force_requested, leader_exited_before_force;
  DWORD leader_before_force, leader_after, descendant_after;
} owner_report;
typedef struct { uint64_t descendant; DWORD magic, stdin_ok; } leaf_report;

static void die(const char *where) {
  DWORD error = GetLastError();
  fprintf(stderr, "%s failed: %lu\n", where, (unsigned long)error);
  if (owned_job) CloseHandle(owned_job);
  if (owned_child.hProcess) {
    TerminateProcess(owned_child.hProcess, 99);
    WaitForSingleObject(owned_child.hProcess, 5000);
  }
  ExitProcess(2);
}
#define REQUIRE(x) do { if (!(x)) die(#x); } while (0)
static void invariant(BOOL condition, const char *where) {
  if (!condition) { SetLastError(ERROR_INVALID_DATA); die(where); }
}
static void write_all(HANDLE pipe, const void *data, DWORD size) {
  DWORD done; REQUIRE(WriteFile(pipe, data, size, &done, NULL));
  invariant(done == size, "short protocol write");
}
static void read_all(HANDLE pipe, void *data, DWORD size) {
  DWORD done; REQUIRE(ReadFile(pipe, data, size, &done, NULL));
  invariant(done == size, "short protocol read");
}
static HANDLE duplicate(HANDLE source_process, HANDLE source, HANDLE target_process, BOOL inherit) {
  HANDLE result;
  REQUIRE(DuplicateHandle(source_process, source, target_process, &result, 0, inherit,
                         DUPLICATE_SAME_ACCESS));
  return result;
}
static HANDLE new_job(DWORD extra) {
  HANDLE job = CreateJobObjectW(NULL, NULL); REQUIRE(job);
  JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits = {0};
  limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | extra;
  REQUIRE(SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits)));
  return job;
}
static DWORD member(HANDLE process, HANDLE job) {
  BOOL yes; REQUIRE(IsProcessInJob(process, job, &yes)); return yes;
}
static void pipe_pair(HANDLE *reader, HANDLE *writer, BOOL child_reads) {
  SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE};
  REQUIRE(CreatePipe(reader, writer, &sa, 0));
  REQUIRE(SetHandleInformation(child_reads ? *writer : *reader, HANDLE_FLAG_INHERIT, 0));
}

/* The only inherited leaf handles are the three stdio endpoints, except the explicit
   positive sentinel control. Attribute failure is reported distinctly from creation. */
static BOOL spawn(WCHAR *command, HANDLE in, HANDLE out, HANDLE err,
                  HANDLE *allowed, SIZE_T count, HANDLE job, DWORD flags,
                  PROCESS_INFORMATION *pi, DWORD *error, DWORD *stage) {
  STARTUPINFOEXW si = {0}; SIZE_T size = 0;
  si.StartupInfo.cb = sizeof(si);
  si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
  si.StartupInfo.hStdInput = in; si.StartupInfo.hStdOutput = out; si.StartupInfo.hStdError = err;
  InitializeProcThreadAttributeList(NULL, job ? 2 : 1, 0, &size);
  si.lpAttributeList = HeapAlloc(GetProcessHeap(), 0, size); REQUIRE(si.lpAttributeList);
  REQUIRE(InitializeProcThreadAttributeList(si.lpAttributeList, job ? 2 : 1, 0, &size));
  BOOL ok = UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                    allowed, sizeof(HANDLE) * count, NULL, NULL);
  *stage = 1;
  if (ok && job)
    ok = UpdateProcThreadAttribute(si.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                                  &job, sizeof(job), NULL, NULL);
  if (ok) {
    *stage = 2;
    ok = CreateProcessW(executable, command, NULL, NULL, TRUE,
                       flags | EXTENDED_STARTUPINFO_PRESENT, NULL, NULL, &si.StartupInfo, pi);
  }
  *error = ok ? 0 : GetLastError();
  DeleteProcThreadAttributeList(si.lpAttributeList);
  HeapFree(GetProcessHeap(), 0, si.lpAttributeList);
  return ok;
}

static int leaf(uint64_t sentinel, const WCHAR *entry_name, DWORD self_exit) {
  HANDLE entry = OpenEventW(EVENT_MODIFY_STATE, FALSE, entry_name); REQUIRE(entry);
  REQUIRE(SetEvent(entry)); CloseHandle(entry);
  /* Try the numeric value, but judge identity using the parent's original event below:
     a child-side handle value may have been reused for a different object. */
  SetEvent((HANDLE)(uintptr_t)sentinel);
  char input[4]; read_all(GetStdHandle(STD_INPUT_HANDLE), input, sizeof(input));
  write_all(GetStdHandle(STD_ERROR_HANDLE), "E", 1);
  HANDLE handles[] = {GetStdHandle(STD_INPUT_HANDLE), GetStdHandle(STD_OUTPUT_HANDLE),
                      GetStdHandle(STD_ERROR_HANDLE)};
  for (int i = 0; i < 3; ++i)
    REQUIRE(SetHandleInformation(handles[i], HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT));
  WCHAR command[MAX_PATH * 4 + 100];
  swprintf_s(command, _countof(command), L"\"%ls\" --descendant", executable);
  PROCESS_INFORMATION descendant = {0}; DWORD error, stage;
  REQUIRE(spawn(command, handles[0], handles[1], handles[2], handles, 3, NULL, 0,
                &descendant, &error, &stage));
  leaf_report report = {(uint64_t)(uintptr_t)descendant.hProcess, 0x4245414d,
                        memcmp(input, "ping", 4) == 0};
  write_all(GetStdHandle(STD_OUTPUT_HANDLE), &report, sizeof(report));
  if (self_exit) {
    char release; read_all(GetStdHandle(STD_INPUT_HANDLE), &release, 1);
    invariant(release == 'X', "invalid ordinary-exit barrier release");
    ExitProcess(self_exit);
  }
  Sleep(INFINITE); return 0;
}

static int owner(HANDLE observer, const WCHAR *mode, const WCHAR *scenario,
                 const WCHAR *entry_name) {
  BOOL atomic = wcscmp(mode, L"job-list") == 0;
  BOOL rejection = wcscmp(scenario, L"assignment-failure") == 0;
  BOOL preassign = wcscmp(scenario, L"preassign-owner-death") == 0;
  BOOL sentinel_control = wcscmp(scenario, L"handle-list-control") == 0;
  BOOL marker_case = wcsncmp(scenario, L"marker-", 7) == 0;
  DWORD self_exit = wcscmp(scenario, L"marker-exit-23") == 0 ? 23 :
    wcscmp(scenario, L"marker-exit-equal") == 0 ? JOB_MARKER : 0;
  owned_job = new_job(0);
  SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE};
  HANDLE sentinel = CreateEventW(&sa, TRUE, FALSE, NULL); REQUIRE(sentinel);
  HANDLE child_in, parent_in, parent_out, child_out, parent_err, child_err;
  pipe_pair(&child_in, &parent_in, TRUE); pipe_pair(&parent_out, &child_out, FALSE);
  pipe_pair(&parent_err, &child_err, FALSE);
  HANDLE allowed[] = {child_in, child_out, child_err, sentinel};
  HANDLE assignment_job = owned_job;
  if (rejection)
    REQUIRE(DuplicateHandle(GetCurrentProcess(), owned_job, GetCurrentProcess(),
                            &assignment_job, JOB_OBJECT_QUERY, FALSE, 0));
  WCHAR command[MAX_PATH * 4 + 250];
  swprintf_s(command, _countof(command), L"\"%ls\" --leaf %llu %ls %lu", executable,
             (unsigned long long)(uintptr_t)sentinel, entry_name, self_exit);
  DWORD error = 0, stage = 0;
  BOOL created = spawn(command, child_in, child_out, child_err, allowed,
                       sentinel_control ? 4 : 3, atomic ? assignment_job : NULL,
                       (!atomic || preassign) ? CREATE_SUSPENDED : 0,
                       &owned_child, &error, &stage);
  CloseHandle(child_in); CloseHandle(child_out); CloseHandle(child_err);
  owner_report report = {0}; report.created = created;
  if (created) {
    report.process = (uint64_t)(uintptr_t)duplicate(GetCurrentProcess(), owned_child.hProcess,
                                                   observer, FALSE);
    if (atomic) report.assigned = member(owned_child.hProcess, owned_job);
    else if (!preassign) {
      report.assigned = AssignProcessToJobObject(assignment_job, owned_child.hProcess);
      if (!report.assigned) { error = GetLastError(); stage = 3; }
    }
  }
  if (rejection) {
    fprintf(stderr, "assignment control: created=%d assigned=%lu error=%lu stage=%lu\n",
            created, report.assigned, error, stage);
    invariant(!created || !report.assigned, "assignment rejection unexpectedly admitted child");
    invariant(error == ERROR_ACCESS_DENIED, "assignment rejection was not access denied");
    if (created) {
      REQUIRE(TerminateProcess(owned_child.hProcess, 81));
      invariant(WaitForSingleObject(owned_child.hProcess, 5000) == WAIT_OBJECT_0,
                "rejected suspended child did not settle");
    }
    report.error = error; report.failure_stage = stage;
    write_all(GetStdHandle(STD_OUTPUT_HANDLE), &report, sizeof(report));
    CloseHandle(owned_job); owned_job = NULL; return 0;
  }
  if (!created) { SetLastError(error); die("CreateProcessW(leaf)"); }
  if (preassign) {
    report.target_child = member(owned_child.hProcess, owned_job);
    write_all(GetStdHandle(STD_OUTPUT_HANDLE), &report, sizeof(report));
    char command_byte; read_all(GetStdHandle(STD_INPUT_HANDLE), &command_byte, 1);
    invariant(FALSE, "preassignment owner was unexpectedly resumed");
  }
  invariant(report.assigned, "child was not assigned before execution");
  if (!atomic) invariant(ResumeThread(owned_child.hThread) == 1, "unexpected suspend count");
  write_all(parent_in, "ping", 4);
  leaf_report child_report; read_all(parent_out, &child_report, sizeof(child_report));
  invariant(child_report.magic == 0x4245414d, "invalid stdout protocol");
  char stderr_byte; read_all(parent_err, &stderr_byte, 1);
  report.stdin_ok = child_report.stdin_ok; report.stderr_ok = stderr_byte == 'E';
  report.sentinel_set = WaitForSingleObject(sentinel, 0) == WAIT_OBJECT_0;
  HANDLE descendant = duplicate(owned_child.hProcess,
                               (HANDLE)(uintptr_t)child_report.descendant,
                               GetCurrentProcess(), FALSE);
  report.descendant = (uint64_t)(uintptr_t)duplicate(GetCurrentProcess(), descendant, observer, FALSE);
  report.target_child = member(owned_child.hProcess, owned_job);
  report.target_descendant = member(descendant, owned_job);
  if (marker_case) {
    report.last_running = WaitForSingleObject(owned_child.hProcess, 0) == WAIT_TIMEOUT;
    invariant(report.last_running, "leader was not running before force barrier");
    REQUIRE(GetExitCodeProcess(owned_child.hProcess, &report.leader_before_force));
    invariant(report.leader_before_force == STILL_ACTIVE, "running leader status mismatch");
  }
  /* Give the coordinator a live-membership snapshot before it releases the force/exit
     barrier, or abruptly terminates this owner. */
  write_all(GetStdHandle(STD_OUTPUT_HANDLE), &report, sizeof(report));
  char command_byte; read_all(GetStdHandle(STD_INPUT_HANDLE), &command_byte, 1);
  invariant(command_byte == 'C', "invalid owner completion command");
  if (marker_case && wcscmp(scenario, L"marker-last-close") != 0) {
    if (self_exit) {
      write_all(parent_in, "X", 1);
      invariant(WaitForSingleObject(owned_child.hProcess, 5000) == WAIT_OBJECT_0,
                "ordinary-exit barrier did not settle leader");
      REQUIRE(GetExitCodeProcess(owned_child.hProcess, &report.leader_before_force));
      invariant(report.leader_before_force == self_exit, "ordinary exit code changed before force");
      report.leader_exited_before_force = 1;
      invariant(WaitForSingleObject(descendant, 0) == WAIT_TIMEOUT,
                "surviving descendant exited before job termination");
    }
    REQUIRE(TerminateJobObject(owned_job, JOB_MARKER));
    report.force_requested = 1;
  }
  /* Keep the explicit-force job handle until collection, so last-close termination
     cannot confound the marker observation. Close-only controls take the other path. */
  if (!report.force_requested) { CloseHandle(owned_job); owned_job = NULL; }
  invariant(WaitForSingleObject(owned_child.hProcess, 5000) == WAIT_OBJECT_0, "child wait failed");
  invariant(WaitForSingleObject(descendant, 5000) == WAIT_OBJECT_0, "descendant wait failed");
  if (marker_case) {
    REQUIRE(GetExitCodeProcess(owned_child.hProcess, &report.leader_after));
    REQUIRE(GetExitCodeProcess(descendant, &report.descendant_after));
    if (owned_job) { CloseHandle(owned_job); owned_job = NULL; }
    write_all(GetStdHandle(STD_OUTPUT_HANDLE), &report, sizeof(report));
  }
  return 0;
}

static int coordinate(const WCHAR *context, const WCHAR *mode, const WCHAR *scenario) {
  BOOL runner_job = member(GetCurrentProcess(), NULL);
  /* A non-inherited outer guardian bounds even the deliberately unassigned suspended
     control if the coordinator exits unexpectedly. It is not the ownership job tested. */
  safety_job = new_job(0); REQUIRE(AssignProcessToJobObject(safety_job, GetCurrentProcess()));
  HANDLE observer = duplicate(GetCurrentProcess(), GetCurrentProcess(), GetCurrentProcess(), TRUE);
  HANDLE child_in, parent_in, parent_out, child_out;
  pipe_pair(&child_in, &parent_in, TRUE); pipe_pair(&parent_out, &child_out, FALSE);
  HANDLE child_err = duplicate(GetCurrentProcess(), GetStdHandle(STD_ERROR_HANDLE),
                               GetCurrentProcess(), TRUE);
  HANDLE allowed[] = {child_in, child_out, child_err, observer};
  WCHAR event_name[128], command[MAX_PATH * 4 + 300];
  swprintf_s(event_name, _countof(event_name), L"Local\\BeamOwnedProbe-%lu-%llu",
             GetCurrentProcessId(), (unsigned long long)GetTickCount64());
  HANDLE entry = CreateEventW(NULL, TRUE, FALSE, event_name); REQUIRE(entry);
  swprintf_s(command, _countof(command), L"\"%ls\" --owner %llu %ls %ls %ls", executable,
             (unsigned long long)(uintptr_t)observer, mode, scenario, event_name);
  DWORD error, stage;
  REQUIRE(spawn(command, child_in, child_out, child_err, allowed, 4, NULL, CREATE_SUSPENDED,
                &owned_child, &error, &stage));
  CloseHandle(child_in); CloseHandle(child_out); CloseHandle(child_err); CloseHandle(observer);
  HANDLE outer = NULL, nested = NULL;
  if (wcscmp(context, L"hosted") != 0) {
    outer = new_job(JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK);
    REQUIRE(AssignProcessToJobObject(outer, owned_child.hProcess));
    if (wcscmp(context, L"nested-nonbreakaway") == 0) {
      nested = new_job(0); REQUIRE(AssignProcessToJobObject(nested, owned_child.hProcess));
    }
  }
  invariant(ResumeThread(owned_child.hThread) == 1, "owner suspend count");
  owner_report report; read_all(parent_out, &report, sizeof(report));
  HANDLE process = (HANDLE)(uintptr_t)report.process;
  HANDLE descendant = (HANDLE)(uintptr_t)report.descendant;
  BOOL rejection = wcscmp(scenario, L"assignment-failure") == 0;
  BOOL preassign = wcscmp(scenario, L"preassign-owner-death") == 0;
  BOOL marker_case = wcsncmp(scenario, L"marker-", 7) == 0;
  BOOL owner_death = wcscmp(scenario, L"owner-death") == 0 || preassign ||
    wcscmp(scenario, L"marker-owner-death") == 0;
  BOOL atomic = wcscmp(mode, L"job-list") == 0;
  DWORD outer_member = process && outer && !rejection ? member(process, outer) : 0;
  DWORD nested_member = process && nested && !rejection ? member(process, nested) : 0;
  DWORD outer_desc = descendant && outer ? member(descendant, outer) : 0;
  DWORD nested_desc = descendant && nested ? member(descendant, nested) : 0;
  BOOL entered = WaitForSingleObject(entry, 0) == WAIT_OBJECT_0;
  if (owner_death) REQUIRE(TerminateProcess(owned_child.hProcess, 73));
  else if (!rejection) write_all(parent_in, "C", 1);
  if (marker_case && !owner_death) {
    owner_report final_report; read_all(parent_out, &final_report, sizeof(final_report));
    invariant(final_report.process == report.process && final_report.descendant == report.descendant,
              "marker protocol changed retained process handles");
    report = final_report;
  }
  invariant(WaitForSingleObject(owned_child.hProcess, 5000) == WAIT_OBJECT_0, "owner did not settle");
  DWORD owner_exit; REQUIRE(GetExitCodeProcess(owned_child.hProcess, &owner_exit));
  invariant(owner_exit == (owner_death ? 73u : 0u), "unexpected owner exit");
  BOOL survivor = FALSE;
  if (process) {
    DWORD waited = WaitForSingleObject(process, preassign && !atomic ? 250 : 5000);
    survivor = waited == WAIT_TIMEOUT;
    invariant(waited == WAIT_OBJECT_0 || (preassign && !atomic && survivor), "child did not settle");
    if (survivor) {
      REQUIRE(TerminateProcess(process, 82));
      invariant(WaitForSingleObject(process, 5000) == WAIT_OBJECT_0, "suspended control cleanup failed");
    }
  }
  if (descendant)
    invariant(WaitForSingleObject(descendant, 5000) == WAIT_OBJECT_0, "descendant survived owner/job closure");
  if (marker_case) {
    invariant(report.last_running, "missing prior live observation");
    REQUIRE(GetExitCodeProcess(process, &report.leader_after));
    REQUIRE(GetExitCodeProcess(descendant, &report.descendant_after));
    if (wcscmp(scenario, L"marker-force") == 0)
      invariant(report.force_requested && !report.leader_exited_before_force &&
                report.leader_after == JOB_MARKER && report.descendant_after == JOB_MARKER,
                "live force did not collect explicit marker");
    else if (wcscmp(scenario, L"marker-exit-23") == 0 ||
             wcscmp(scenario, L"marker-exit-equal") == 0) {
      DWORD expected = wcscmp(scenario, L"marker-exit-23") == 0 ? 23 : JOB_MARKER;
      invariant(report.force_requested && report.leader_exited_before_force &&
                report.leader_before_force == expected && report.leader_after == expected &&
                report.descendant_after == JOB_MARKER,
                "force replaced an already-collected ordinary leader exit");
    } else invariant(!report.force_requested, "close/death control explicitly forced the job");
  }
  if (rejection) invariant(!entered && report.error == ERROR_ACCESS_DENIED, "rejected child entered code");
  else if (preassign)
    invariant(!entered && report.target_child == (DWORD)atomic && survivor == !atomic,
              "preassignment comparison mismatch");
  else {
    invariant(entered && report.stdin_ok && report.stderr_ok && report.target_child &&
              report.target_descendant, "stdio or ownership control failed");
    invariant(report.sentinel_set == (DWORD)(wcscmp(scenario, L"handle-list-control") == 0),
              "HANDLE_LIST sentinel identity mismatch");
    if (outer) invariant(outer_member == (DWORD)(nested != NULL) && outer_desc == outer_member,
                         "silent outer inheritance mismatch");
    if (nested) invariant(nested_member && nested_desc, "nested nonbreakaway membership lost");
  }
  printf("{\"context\":\"%ls\",\"mode\":\"%ls\",\"case\":\"%ls\","
         "\"runner_in_job\":%lu,\"guard_job\":true,\"created\":%lu,\"assigned\":%lu,"
         "\"error\":%lu,\"failure_stage\":%lu,\"entered\":%u,"
         "\"stdin_ok\":%lu,\"stderr_ok\":%lu,\"sentinel_set\":%lu,"
         "\"target_child\":%lu,\"target_descendant\":%lu,"
         "\"outer_child\":%lu,\"outer_descendant\":%lu,"
         "\"nested_child\":%lu,\"nested_descendant\":%lu,"
         "\"job_marker\":%lu,\"last_running\":%lu,\"force_requested\":%lu,"
         "\"leader_exited_before_force\":%lu,\"leader_before_force\":%lu,"
         "\"leader_after\":%lu,\"descendant_after\":%lu,"
         "\"preassignment_survivor\":%u,\"settled_after_cleanup\":true,\"result\":\"passed\"}\n",
         context, mode, scenario, (DWORD)runner_job, report.created, report.assigned,
         report.error, report.failure_stage, (unsigned)entered, report.stdin_ok, report.stderr_ok,
         report.sentinel_set, report.target_child, report.target_descendant, outer_member,
         outer_desc, nested_member, nested_desc, JOB_MARKER, report.last_running,
         report.force_requested, report.leader_exited_before_force, report.leader_before_force,
         report.leader_after, report.descendant_after, (unsigned)survivor);
  fflush(stdout);
  /* Do not close safety_job while this coordinator is still running in it. ExitProcess
     closes the last non-inherited guardian handle and settles any unexpected remainder. */
  return 0;
}

int wmain(int argc, WCHAR **argv) {
  DWORD name_size = GetModuleFileNameW(NULL, executable, _countof(executable));
  REQUIRE(name_size && name_size < _countof(executable));
  if (argc == 2 && wcscmp(argv[1], L"--descendant") == 0) { Sleep(INFINITE); return 0; }
  if (argc == 5 && wcscmp(argv[1], L"--leaf") == 0)
    return leaf(_wcstoui64(argv[2], NULL, 10), argv[3], (DWORD)_wcstoui64(argv[4], NULL, 10));
  if (argc == 6 && wcscmp(argv[1], L"--owner") == 0)
    return owner((HANDLE)(uintptr_t)_wcstoui64(argv[2], NULL, 10), argv[3], argv[4], argv[5]);
  if (argc == 4) return coordinate(argv[1], argv[2], argv[3]);
  fputs("expected CONTEXT MODE CASE\n", stderr); return 2;
}
