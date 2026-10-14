# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-rust-app,tests/app/terminal-probe,hyper-terminal-probe,terminal-probe,bin/terminal-probe,development))
