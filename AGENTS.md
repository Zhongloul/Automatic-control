# 项目约定

## 自动验证（必须）
每次修改代码后，必须自动在后台运行完整验证，不需要用户提醒：

1. `python tools\self_test.py`
2. `python tools\replay_simulate_test.py`
3. `python tools\gui_smoke_test.py`

以退出码和 PASS/FAIL、`GUI_SMOKE_OK` 摘要判断结果；任何失败都必须先修复并重跑，全部通过后才能交付。交付时说明验证结果。

- GUI 测试必须隐藏窗口运行（`gui_smoke_test.py` 已设置 `KILO_HIDDEN_UI=1`），禁止测试窗口弹到前台、抢占焦点或移动鼠标，不能打断用户正常工作。
- 只有交付打包时可以启动新版本 exe；不要为了测试反复启动前台窗口。

## 打包约定
- 版本号写入 `VERSION`，用 `build_exe.bat` 或 PyInstaller 打包。
- 根目录只保留最新版本 exe，旧版本移动到 `旧版本\`。
- 打包成功后自动启动新版本，并确认窗口已出现。

## Git 操作
- 不要自动 commit、push 或创建 PR；只有用户明确要求时才执行。
- 日常修改、测试和打包照常进行，但不要每次更新都推送 GitHub。
- 用户明确要求推送时，必须把根目录最新版本 exe 一并提交推送；旧版本 exe 不提交。
