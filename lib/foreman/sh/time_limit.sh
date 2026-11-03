#!/bin/bash
# Runs a command with a time limit (macOS has no `timeout`). Copied unchanged from an earlier in-house version.
#   foreman time-limit SECONDS cmd args...
# On timeout it kills the command's whole process group (TERM and, 5 s later, KILL), exits 124 and says so on stderr.
# Otherwise it exits with the command's code. The command runs in its own process group so that a `bash -c` and its
# children die together. TERM/INT/HUP sent to this script are passed to the group.
set -u
[ $# -ge 2 ] || { sed -n '2,5p' "$0"; exit 2; }
exec perl -e '
    my $secs = shift; my $pid = fork // die "fork: $!\n";
    if (!$pid) { setpgrp(0, 0); exec @ARGV or die "time-limit: cannot run $ARGV[0]: $!\n" }
    sub stop {
        kill "TERM", -$pid;
        for (1..50) { last if waitpid($pid, 1) > 0; select undef, undef, undef, 0.1 }
        kill "KILL", -$pid; waitpid $pid, 0;
    }
    for my $s (qw(TERM INT HUP)) { $SIG{$s} = sub { stop(); exit 128 + ($s eq "HUP" ? 1 : $s eq "INT" ? 2 : 15) } }
    local $SIG{ALRM} = sub { stop(); print STDERR "time-limit: cut after $secs s: @ARGV\n"; exit 124 };
    alarm $secs; waitpid $pid, 0; alarm 0;
    exit($? & 127 ? 128 + ($? & 127) : $? >> 8);
' "$@"
