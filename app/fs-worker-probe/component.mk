# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-rust-app,tests/app/fs-worker-probe,hyper-fs-worker-probe,fs-worker-probe,svc/fs-worker-probe,development))
