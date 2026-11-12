/* SPDX-License-Identifier: MIT */
/* Userspace fixtures only. Never include in production kernel sources. */
#ifndef ASAHI_FIXTURE_EXPECTED_FAILURE_H
#define ASAHI_FIXTURE_EXPECTED_FAILURE_H

#include <signal.h>
#include <stdlib.h>

/* Mutants intentionally trip assertions and invalid-access checks. Preserve a
 * nonzero result without a fatal-signal/core event for the desktop watcher.
 * The unchanged fixture still has to exit zero. This changes no system policy.
 */
static void asahi_fixture_failed_signal(int signal_number)
{
	_Exit(128 + signal_number);
}

static void __attribute__((constructor)) asahi_fixture_expected_failure_init(void)
{
	if (signal(SIGABRT, asahi_fixture_failed_signal) == SIG_ERR ||
	    signal(SIGSEGV, asahi_fixture_failed_signal) == SIG_ERR ||
	    signal(SIGILL, asahi_fixture_failed_signal) == SIG_ERR ||
	    signal(SIGFPE, asahi_fixture_failed_signal) == SIG_ERR)
		_Exit(125);
#ifdef SIGBUS
	if (signal(SIGBUS, asahi_fixture_failed_signal) == SIG_ERR)
		_Exit(125);
#endif
}

#endif
