/* SPDX-FileCopyrightText: 2026 roolrz
 * SPDX-License-Identifier: Apache-2.0
 */

#include <hyper/startup.h>
#include <hyper/syscall.h>
#include <string.h>

#define STANDARD_OUTPUT_PURPOSE UINT32_C(0x80030002)

/* Keep the static executable contract independent of ordinary tools' Rust
 * dependencies. The Rust static/std path has its own std-test-static fixture. */
int hyper_main(const hyper_startup_t *startup)
{
	hyper_native_handle_t output = 0;
	if (hyper_startup_find_handle(startup,
			STANDARD_OUTPUT_PURPOSE, &output))
		return 1;
	for (size_t i = 1; i < startup->argument_count; ++i) {
		if (i > 1 && hyper_byte_channel_write(output, " ", 1))
			return 1;
		const char *argument = startup->arguments[i];
		if (hyper_byte_channel_write(output, argument, strlen(argument)))
			return 1;
	}
	return hyper_byte_channel_write(output, "\n", 1) ? 1 : 0;
}
