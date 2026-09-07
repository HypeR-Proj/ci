/* SPDX-FileCopyrightText: 2026 roolrz
 * SPDX-License-Identifier: Apache-2.0
 */

#include <hyper/dlfcn.h>
#include <hyper/startup.h>
#include <hyper/syscall.h>
#include <stddef.h>
#include <stdint.h>

#define STANDARD_OUTPUT_PURPOSE UINT32_C(0x80030002)
#define PROBE_VALUE UINT64_C(0x48595045525f444c)

typedef uint64_t (*probe_function_t)(void);

static int write_result(hyper_native_handle_t output, const char *message, size_t length)
{
    return hyper_byte_channel_write(output, message, length) == HYPER_NATIVE_STATUS_OK ? 0 : 1;
}

int hyper_main(const hyper_startup_t *startup)
{
    static const char success[] = "HYPER_DYNAMIC_LINK_OK\n";
    hyper_native_handle_t directory = 0;
    hyper_native_handle_t output = 0;
    if (hyper_startup_find_handle(
            startup,
            HYPER_NATIVE_STARTUP_HANDLE_PURPOSE_DYNAMIC_LIBRARY_DIRECTORY,
            &directory) != HYPER_NATIVE_STATUS_OK
        || hyper_startup_find_handle(startup, STANDARD_OUTPUT_PURPOSE, &output)
            != HYPER_NATIVE_STATUS_OK) {
        return 1;
    }
    void *object = hyper_dlopen_at(directory, "libdynamic-probe.so", HYPER_RTLD_NOW | HYPER_RTLD_LOCAL);
    if (object == NULL) {
        return 1;
    }
    void *symbol = hyper_dlsym(object, "hyper_dynamic_probe");
    if (symbol == NULL) {
        (void)hyper_dlclose(object);
        return 1;
    }
    probe_function_t probe = (probe_function_t)symbol;
    int status = probe() == PROBE_VALUE
        ? write_result(output, success, sizeof(success) - 1) : 1;
    if (hyper_dlclose(object) != HYPER_NATIVE_STATUS_OK) {
        status = 1;
    }
    return status;
}
