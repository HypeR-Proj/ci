# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-file,{apps}/echo-static,bin/echo-static,0755,development))
$(eval $(call native-file,{apps}/dynamic-test,bin/dynamic-test,0755,development))
$(eval $(call native-file,{std}/std-dynamic,bin/std-test,0755,development))
$(eval $(call native-file,{std}/std-static,bin/std-test-static,0755,development))
$(eval $(call native-file,{apps}/libdynamic-probe.so,lib64/{arch}-hyper-hyper/libdynamic-probe.so,0755,development))
