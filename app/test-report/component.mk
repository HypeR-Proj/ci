# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-rust-app,tests/app/test-report,hyper-test-report,test-report,bin/test-report,development))
