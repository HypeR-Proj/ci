/* SPDX-FileCopyrightText: 2026 roolrz
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdint.h>

#define PROBE_VALUE UINT64_C(0x48595045525f444c)

static uint64_t probe_state;

static void initialize_probe(void) __attribute__((constructor));
static void initialize_probe(void)
{
    probe_state = PROBE_VALUE;
}

__attribute__((visibility("default"))) uint64_t hyper_dynamic_probe(void)
{
    return probe_state;
}
