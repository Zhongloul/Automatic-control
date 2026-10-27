# 通用自动化控制台

面向窗口化目标的纯黑盒自动化框架：**截屏识别 + 行为决策 + 键鼠注入**，不修改目标程序、不读取内存。可录制/回放固定操作，也可用视觉识别与流程导航自动执行重复任务，适用于游戏、模拟器、桌面软件等窗口化程序。

提供版本化图形控制台 `自动化控制台_v*.exe`：实时预览识别结果、一键开始/暂停、内置校准与目标模板抓取，无需命令行。同时保留命令行入口用于调试和批量运行。

> 仅用于你拥有或已获授权的目标程序。对在线游戏/第三方服务使用自动化可能违反用户协议，请自行评估。

## 功能

- 纯黑盒：不修改目标文件、不读内存，只通过画面和键盘工作
- 识别：状态条1/状态条2、快捷栏冷却、目标位置（模板匹配）、主体位置、场景
- 决策：Y 轴对齐 → X 轴接近 → 快捷操作评分 → 状态不足恢复 → 受击后撤 → 无目标时寻路
- 流程导航：场景模板识别 + 黑屏加载检测 + 场景跳转执行（等待/重试/超时），模板图片可后补
- 拟人化：决策间隔抖动、按键时长抖动、瞄准误差、随机犹豫、偶尔跳一下
- 图形界面：EXE 双击运行，实时叠加层、状态面板、运行日志、GUI 内校准/抓模板
- 安全：仅目标窗口在前台时发送按键、F8/F9/F1 全局热键、仅观察模式、`--dry-run` 只观察不操作
- 任务中心：选择“自动操作 / 流程导航 / 录制回放”，显示运行前检查和缺少的配置，一键开始
- 场景与流程管理：EXE 内新增场景、从目标窗口抓图或导入图片、框选标记、增删改跳转、排序、导入/导出流程
- 录制回放：录制人类键鼠操作，支持速度、循环与间隔、前台暂停、画面状态断点与条件分支
- 工具链：UI 校准、截图、环境自检、离线仿真测试、运行日志

## 当前完成度

| 功能 | 状态 |
|---|---|
| 智能操作（单窗口） | 可用 |
| 场景识别（加载检测/模板/确认） | 可用，需添加图片模板 |
| 场景模板 GUI 管理（抓图/导入/框选） | 可用 |
| 跳转流程 GUI 管理（增删改/排序/导入导出） | 可用 |
| 流程导航任务 | 可用，需场景模板 + 跳转流程 |
| 录制 / 回放 | 可用 |
| 完整流程循环（清理→寻找出口→结算→重开） | 开发中 |
| 训练模型（行为克隆等） | 未开始 |

## 目录结构

```
自动化操作/
├── 自动化控制台_v*.exe     版本化图形主程序（打包产物，双击运行）
├── VERSION                 当前版本号（打包时读取）
├── start_latest.bat        关闭旧 EXE 并启动最新版本
├── gui.py                  图形界面源码入口
├── main.py                 命令行入口
├── config.json             配置文件（首次运行自动生成）
├── requirements.txt
├── gui.bat                 从源码启动图形界面
├── start.bat               命令行启动
├── calibrate.bat           命令行校准
├── self_test.bat           环境自检
├── simulate.bat            离线仿真测试
├── build_exe.bat           重新打包 EXE
├── assets/app.ico          程序图标
├── src/
│   ├── config.py           配置加载与默认值
│   ├── window.py           窗口查找/前台检测/客户区坐标
│   ├── capture.py          截屏（dxcam 优先，回退 GDI/mss）+ 采集线程
│   ├── input.py            SendInput 扫描码键鼠注入
│   ├── perception.py       状态条/快捷操作/目标/主体识别
│   ├── scene.py            场景识别（模板/pHash/加载检测/确认）
│   ├── scene_ui.py         场景模板与跳转流程管理界面
│   ├── map_graph.py        场景图、跳转执行与路线规划
│   ├── decision.py         操作状态机与快捷操作评分
│   ├── humanize.py         拟人化时序
│   ├── engine.py           线程化自动化引擎（图形界面使用）
│   ├── recorder.py         键鼠录制器（前台采样、会话保存、画面采样）
│   ├── replay.py           回放器（时间线/循环/前台恢复/画面状态断点）
│   ├── keyframes.py        录制关键帧、画面状态签名与参考图
│   ├── analyzer.py         自动断点建议与批量写入
│   ├── analyze_ui.py       自动断点建议界面
│   ├── session_store.py    录制会话读写
│   ├── hotkeys.py          全局热键检测
│   ├── imageio.py          中文路径安全图片读写
│   ├── overlay.py          调试叠加层绘制
│   └── debug_view.py       命令行调试窗口
├── tools/
│   ├── calibrate.py        UI 校准工具（框选状态条、快捷栏、抓目标模板）
│   ├── snapshot.py         抓一帧截图
│   ├── self_test.py        环境自检
│   ├── gui_smoke_test.py   图形界面冒烟测试
│   ├── scene_test.py       场景识别/流程跳转测试工具
│   ├── scene_simulate_test.py  场景与流程跳转离线测试
│   ├── replay_simulate_test.py 录制/回放离线测试
│   ├── record_gui_smoke_test.py 录制 GUI 冒烟测试
│   └── simulate_test.py    离线仿真测试（不需要目标程序）
├── templates/
│   ├── enemy/              目标模板（校准工具生成）
│   ├── player/             主体模板（可选）
│   └── scenes/             场景模板（<场景名>/*.png）
├── sessions/               录制回放会话
├── captures/               截图输出
├── build/                  PyInstaller 打包工作目录
└── logs/                   运行日志
```

## 从 GitHub 开发

```bat
git clone https://github.com/Zhongloul/Automatic-control.git
cd Automatic-control
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python gui.py
```

- 仓库只包含源码、配置模板、资源与测试；EXE、旧版本、录制会话、日志、截图和 build 产物不会提交。
- 打包依赖：`python -m pip install -r requirements-dev.txt`，然后运行 `build_exe.bat`。
- 自动验证：`python tools\self_test.py`、`python tools\replay_simulate_test.py`、`python tools\gui_smoke_test.py`。
- 公开仓库可直接克隆、Fork 和提交 PR；如果要直接 push 到本仓库，需要仓库所有者在 Settings → Collaborators 里添加你，认证使用你自己的 GitHub 账号（SSH key 或浏览器登录/PAT），不需要共享别人的密钥。

## 环境要求

- Windows 10/11
- **直接使用 EXE：不需要安装 Python**
- 从源码运行：Python 3.10+（已在 Python 3.14 验证），执行 `python -m pip install -r requirements.txt`
- 目标程序使用**窗口化或无边框窗口模式**，分辨率固定，运行期间不要缩放窗口
- 不建议目标窗口被其他窗口完全遮挡；独占全屏大概率截不到画面

可选：`python -m pip install dxcam` 可显著降低截屏耗时（如果你的 Python 版本有对应 wheel）。未安装时自动回退 GDI（不带 `CAPTUREBLT`，避免光标闪烁），再回退 mss。

## 快速开始（图形界面）

1. 启动目标程序，进入能看到状态条、快捷栏和可操作内容的界面，保持窗口化/无边框。
2. 双击最新的 `自动化控制台_v*.exe`（或运行 `start_latest.bat`），默认打开“任务中心”。
3. 顶部填写目标窗口标题关键字（或点“枚举窗口”双击选择），点“锁定窗口”。右侧检查项里的“目标窗口”应变成“已锁定”。
4. 在任务列表选择“智能操作（单窗口）”，按“运行前检查”逐项补齐：
   - 点“校准 HUD”：框选 状态条1/状态条2/快捷栏、点击主体位置、抓取目标模板；
   - 检查项变为绿色后，“开始任务”按钮可用。
5. 勾选“仅观察”，点“开始任务”并切回目标窗口，观察叠加层和任务状态；确认识别正常后关闭“仅观察”正式运行。
6. 需要流程导航时，点“场景 / 流程管理”：
   - “场景模板”页：新增场景 → 从目标画面抓取或导入图片 → 框选场景名/场景 UI → 保存并应用；
   - “跳转流程”页：新增跳转，填写起点/终点和动作，调整顺序，导入/导出 JSON；
   - 回到任务中心，选择“流程导航”和目标场景，点击“开始任务”。
7. 想录制手动操作：切到“录制回放”页，点“开始录制”（或 F6），倒计时后手动执行流程；切图后按 F5 插入同步断点，按 F7 停止并保存。录制时会以 4 fps 自动保存画面，录制完可点“自动加断点”让工具分析空闲段并生成画面状态断点。选择会话后设置速度、循环次数（0=无限）、循环间隔和“断点后立即继续”，点“开始回放”。
8. F8 开始/继续，F9 停止当前任务，F1 开关叠加层。日志在“实时监控”页，或写入 `logs/gui_*.log`。

> 自动化发送按键时目标窗口必须在前台。图形界面可以放在第二块屏幕或后台运行，通过热键控制。

## 图形界面说明

**任务中心**（默认页）：

- 左侧任务列表：自动操作、流程导航、录制回放、开发中任务（会标注 `[开发中]`）
- 右侧显示任务说明、使用步骤、运行前检查、目标场景选择、状态
- 缺少配置时“开始任务”不可用，并明确提示缺什么（窗口/状态条/目标模板/场景/流程/录制会话）
- `[就绪]` / `[缺配置]` 标签让用户一眼看到当前能不能跑

**录制回放**页：

- 左侧会话列表：录制时间、时长、事件数
- 双击会话可重命名；分支目标引用会自动同步更新
- 录制：F6 开始倒计时、F7 停止并保存；只记录目标窗口前台时的键鼠，切出目标窗口会自动暂停
- 停止时自动去头去尾：从第一个键鼠操作开始，到最后一个操作结束，去掉前面倒计时/空闲和结束后多余的时间与画面
- 回放：速度 0.25x~4x、循环次数（0=无限）、循环间隔
- 事件预览：查看录制的前 300 条按键/点击事件
- 支持删除会话、打开 `sessions/` 目录

**实时监控**页左侧为实时画面叠加层：

- 黄色框：HP 条（左上角显示识别到的血量比例）
- 蓝色框：MP 条
- 绿色/灰色小框：快捷操作格，绿色=可用，灰色=冷却中
- 黄色方框 + 分数：识别到的目标（模板匹配置信度）
- 红色十字：主体参考点
- 灰色细框：目标搜索区域

右侧状态面板：运行状态、截屏后端与 FPS、状态1/状态2、目标数、快捷项就绪数、当前场景、当前动作。下方日志实时显示暂停原因、窗口状态和识别异常。

“校准 / 抓取模板”窗口：

- 步骤列表可点击切换，虚线框为已保存的 ROI
- 鼠标拖拽框选，单击记录主体位置，绿色实线框为当前选择
- 目标模板按当前分辨率保存到 `templates/enemy/`，保存后立即重新加载
- 关闭时如有未保存修改会提示

“场景 / 流程管理”窗口：

- 场景模板页：新增场景、从目标画面抓取标记、导入图片、整张图片作为标记、删除标记/场景、预览、测试当前画面
- 跳转流程页：新增/编辑/删除跳转、上移/下移调整顺序、双击编辑、导入/导出 JSON、启用流程跳转
- 所有修改点击“保存并应用”后写入 `config.json` 并重载引擎；未保存关闭会提示

## 热键

| 按键 | 功能 |
|---|---|
| F8 | 自动操作开始 / 继续 / 暂停 |
| F9 | 立即停止当前回放、录制、流程任务或自动操作 |
| F1 | 开关调试叠加层 |
| F6 | 开始 / 停止录制（全局） |
| F7 | 停止录制并保存（全局） |
| F5 | 录制中插入同步断点（切图后/采药点） |

热键可在 `config.json` 的 `hotkeys` 中改成 F1~F12 中任意键。命令行模式下 F9 为退出。

## 录制回放

### 使用步骤

1. 锁定目标窗口，切到“录制回放”页
2. 点“开始录制”或按 F6，倒计时后切到目标窗口
3. 手动打一遍流程（走路、放快捷操作、点门、开关菜单都可以）
4. 按 F7 停止并保存；会话会自动出现在左侧列表
5. 切图后或采药点按 F5 插入同步断点（可选但推荐）
6. 选择会话，设置速度、循环次数（0 为无限）、循环间隔，点“开始回放”
7. 任务中心的“录制回放”任务也会使用当前选中的会话

### 录制行为

- 只记录**目标窗口在前台**时的键盘和鼠标点击
- 切出目标窗口自动暂停录制，回到目标窗口继续，不会把切窗的空档算进时间线
- 默认记录字母、数字、方向键、空格、Shift/Ctrl/Alt、Enter/Tab
- 如果目标软件快捷键用了其他键（例如 F1、小键盘），在 `config.json` 的 `recorder.keys` 里追加
- 鼠标移动默认不记录，只记录点击；需要鼠标轨迹时把 `record_mouse_move` 设为 `true`
- 录制时会以 4 fps 保存画面帧（`recorder.frame_sample_hz` 可调），用于自动断点和 sync 参考帧
- 默认开启去头去尾（`recorder.trim_idle`）：停止时裁掉第一个键鼠操作前、最后一个操作后的时间和画面；鼠标移动不计入首尾边界

### 回放行为

- 按录制时间线执行，可调 0.25x~4x 速度
- 支持循环：循环次数填 0 表示无限回放，循环间隔可设置
- 目标窗口失去前台时自动暂停并释放按键，回到目标窗口后从当前时间点继续
- 停止/异常时立即释放所有键鼠，避免卡键
- 点击坐标使用客户区相对比例，窗口位置变化不影响；但仍建议固定分辨率

### 加载/切图中断怎么处理

纯时间线回放无法知道目标程序有没有真正加载完，用断点解决：

1. **sync 断点**：录制时触发切图后按 F5。工具会在你下一次键鼠操作时，自动把“操作前那一刻”的画面存为参考帧，并挂到该 `sync` 断点上。回放时等待画面恢复到参考帧，再执行后续动作；不再依赖黑屏检测。没有学习到参考帧时只做短暂停顿。
2. **断点后立即继续**（默认开启）：断点条件满足后，下一事件立即执行，不再等待录制时的空闲时间。

断点事件格式：

```json
{"t":1.05,"type":"checkpoint","name":"sync","ref":"state_refs/sync_1.05.jpg","threshold":0.08}
{"t":1.20,"type":"checkpoint","name":"wait_visual","source":"scene","scene":"dungeon_1"}
{"t":3.30,"type":"checkpoint","name":"wait_visual","source":"image","ref":"state_refs/state_01_11.00.jpg","threshold":0.08}
{"t":4.00,"type":"checkpoint","name":"wait_seconds","seconds":1.5}
{"t":12.0,"type":"checkpoint","name":"branch","timeout_s":15,
 "cases":[
   {"source":"scene","scene":"result_ok","goto":{"session":"replay_success"}},
   {"source":"image","ref":"state_refs/result_fail.jpg","threshold":0.10,"goto":{"session":"replay_fail"}}
 ],
 "default":{"goto":{"session":"replay_retry"}}}
```

- `sync`：自动学习参考帧并等待画面匹配；未学习到参考帧时只做短暂停顿
- `wait_visual`：视觉等待，`source=scene` 用场景标记匹配，`source=image` 用参考图匹配
- `wait_seconds`：固定等待
- `branch`：条件分支，按顺序检查 `cases`，命中后切换目标会话并从其开头继续；超时走 `default`，没有默认则停止。目标会话必须是 `sessions/` 下已存在的另一个录制会话

匹配阈值说明：参考图和当前画面都会缩成 96x54 灰度签名，再用平均绝对差比较。`threshold` 范围 0.0001~1.0，**越小越严格**（不容易误判，但可能一直等不到），**越大越宽松**（更容易匹配，但可能提前放行或匹配错状态）。默认 0.10，自动加断点生成的阈值一般在 0.05~0.22；匹配不上就调大，匹配过早/误判就调小。

条件分支的使用：先分别录制成功、失败、重试三条结果路径为独立会话，再在主流程决策点添加 `branch` 断点，为每个结果配置“条件 → 目标会话”，可选配置超时默认路径。分支切换后当前会话结束，目标会话播完后本轮结束；多轮回放会重新从主流程开始。

旧会话里的 `wait_scene` / `wait_state` 仍可正常回放，编辑后会统一转为 `wait_visual`。

采药点可以按 F5 插 `sync` 断点；如果要做“拾取结果确认”，还需要增加提示模板或背包数量检测。

### 自动加断点

录制时工具会以 4 fps 保存目标画面（`frames/`）。在“录制回放”页选中会话后点“自动加断点”：

1. 工具找出相邻动作之间超过“最小空闲(s)”的长空闲段（默认 1.5 秒，可在窗口里修改）
2. 截取“下一动作发生前”的画面作为参考图，保存到 `state_refs/`
3. 生成 `wait_visual`（参考图）断点建议，可在窗口里勾选后批量写入会话
4. 回放到断点时等待画面与参考图匹配，再立即执行下一动作（`checkpoint_immediate`）

旧版本录制的会话没有画面数据，无法自动分析；重新录制一次即可。阈值可在“编辑断点”里微调：等待过早放宽阈值，等待超时则改大或重新录制。

### 断点编辑界面

不需要手动改 JSON：

1. “录制回放”页选择会话，点“编辑断点”
2. 左侧时间线选择插入位置（会自动填入时间 +0.001s）
3. 右侧选择类型 `sync` / `wait_visual` / `wait_seconds` / `branch`
4. `wait_visual` 先选来源：场景标记（选场景 ID）或参考图（选图片并设置阈值）；`wait_seconds` 填等待秒数
5. `branch` 点“编辑分支...”，添加多个“条件 → 目标会话”，可设置超时默认路径
6. 点“新增断点”或“应用修改”
7. 点“写入会话”保存；原文件备份为 `events.jsonl.bak`
8. 再开始回放即可生效

编辑窗口只显示当前类型需要的配置：`sync` 不显示额外配置，`wait_seconds` 只显示秒数，`wait_visual` 按“场景/参考图”显示对应字段，`branch` 只显示分支配置。

编辑窗口支持列出全部事件、查看已有断点、删除断点；旧 `wait_scene` / `wait_state` 会自动映射为 `wait_visual`。`wait_visual` 的场景列表只显示已经有标记图片的场景。

### 会话格式

```
sessions/replay_YYYYmmdd_HHMMSS/
├── meta.json       窗口、时长、事件数、录制键位、画面帧数
├── events.jsonl    每行一个事件，t 为相对秒数
├── frames/         录制画面（4 fps，index.jsonl 记录时间戳）
└── state_refs/     自动断点使用的参考画面
```

```json
{"t":0.000,"type":"key_down","key":"d"}
{"t":0.080,"type":"key_up","key":"d"}
{"t":0.300,"type":"mouse_down","button":"left","pos":[0.52,0.63]}
{"t":0.350,"type":"mouse_up","button":"left","pos":[0.52,0.63]}
```

### 注意

- 目标窗口内改键后，回放仍然发送录制时的按键名；如果目标软件键位变了需要重录
- 录制前不要在目标窗口里输入账号密码；录制只覆盖目标窗口前台时的输入
- 回放仍是纯外部 SendInput；如果反作弊拦截模拟输入，回放同样不可用
- 离线测试：`python tools\replay_simulate_test.py`

## 命令行模式（可选）

```powershell
python tools\simulate_test.py           # 离线仿真，验证感知/决策
python tools\self_test.py               # 环境自检
python tools\calibrate.py --title 窗口标题
python tools\snapshot.py --title 窗口标题
python main.py --dry-run                # 只观察不按键
python main.py                          # 正式运行，F8 开始，F9 退出
```

CLI 的调试窗口由 OpenCV 提供；图形界面使用独立的 EXE，两者共用同一份 `config.json` 和 `templates/`。

热键可在 `config.json` 的 `hotkeys` 中改成 F1~F12 中任意键。

## 工作原理

每个决策周期（约 8~14 Hz，带随机抖动）：

```
截屏线程持续抓帧
      ↓
感知 Perception
  状态条颜色占比 / 快捷操作格灰度 / 目标模板匹配 / 主体锚点
      ↓
决策 CombatBrain（优先级状态机）
  1. HP 骤降 → 后撤
  2. HP 低于阈值 → 喝药
  3. 无目标 → 随机方向寻路（偶尔跳跃）
  4. |Δy| 超容差 → 上下对齐（2D 横版的关键）
  5. |Δx| 超攻击距离 → 左右接近
  6. 有可用快捷操作 → 按权重随机择优释放
  7. 否则普攻
      ↓
执行 InputController
  SendInput + 扫描码，按键时长/间隔随机化
```

所有画面坐标都以截屏帧为基准。由于 2D 横版镜头通常跟随主体，目标与主体的**屏幕相对偏移**可直接当作目标窗口内相对距离使用。

## 流程导航与场景识别

流程导航逻辑和 GUI 管理都已实现。推荐直接在 EXE 的“场景 / 流程管理”里完成，不需要手动改文件夹：

1. “场景模板”页点“新增场景”，输入场景 ID（如 `town`、`dungeon_1`）
2. 点“从目标画面抓取”或“导入图片”
3. 在截图上框住场景名/场景 UI 等固定标记，点“保存标记”
4. 点“保存并应用”，场景识别立即重载
5. 在“跳转流程”页新增跳转，填写起点/终点和动作，用上移/下移调整顺序
6. 回到任务中心选择“流程导航”，选择目标场景并开始

也可以手动放文件，目录约定：

```
templates/scenes/
├── town/
│   └── marker_1.png
└── dungeon_1/
    └── marker_1.png
```

- 子目录名就是场景 ID
- 目录内所有 PNG/JPG 自动作为该场景的识别模板
- 模板应抓**固定出现**的元素：场景名、流程名、小场景标题、场景专属 UI；不要用会消失的门/怪
- 在 `scenes.profiles` 里给模板配置 ROI，可显著提速并减少误识别

配置示例：

```json
"scenes": {
  "default_threshold": 0.82,
  "confirm_frames": 2,
  "poll_interval_s": 0.2,
  "loading": {
    "enabled": true,
    "max_mean_brightness": 18.0,
    "max_std": 12.0,
    "confirm_frames": 3
  },
  "profiles": {
    "town": {
      "threshold": 0.85,
      "templates": [
        {"image": "templates/scenes/town/title.png", "roi": [0.03, 0.02, 0.3, 0.06]}
      ]
    }
  }
},
"map_graph": {
  "enabled": false,
  "transitions": [
    {
      "name": "town_to_dungeon_1",
      "from": "town",
      "to": "dungeon_1",
      "actions": [
        {"type": "click", "pos": [0.52, 0.63], "button": "left"},
        {"type": "wait", "seconds": 0.5}
      ],
      "retries": 1,
      "require_loading": true,
      "from_timeout_s": 6,
      "loading_timeout_s": 8,
      "scene_timeout_s": 25
    }
  ]
}
```

支持的动作类型：

| type | 参数 | 说明 |
|---|---|---|
| `key_tap` / `key` | `key`, `hold_s` | 点按一次 |
| `key_hold` | `key`, `seconds` | 按住一段时间 |
| `wait` | `seconds` | 等待 |
| `click` | `pos`, `button` | 点击客户区相对坐标 |
| `move` | `pos` | 移动鼠标 |

工作方式：

- 场景识别：模板匹配 / pHash 打分 + 连续 N 帧确认 + 黑屏加载检测
- 跳转流程：等待起始场景 → 执行动作 → 可选等待加载 → 等待目标场景 → 超时重试
- 流程列表顺序 = 同一起点存在多条跳转时的优先级，可用上移/下移调整
- 任务中心“流程导航”已接入跳转执行；完整流程循环尚未接入
- 在管理器中“保存并应用”会自动重载识别；手动改文件后重启 EXE 或点“锁定窗口”重载
- 实时监控页和任务中心都会显示当前场景

测试工具：

```powershell
python tools\scene_test.py --list
python tools\scene_test.py --image captures\test.png
python tools\scene_test.py --wait dungeon_1 --timeout 30
python tools\scene_test.py --transition town_to_dungeon_1
python tools\scene_test.py --transition town_to_dungeon_1 --execute
python tools\scene_test.py --route dungeon_1 --execute
```

没有场景模板时场景识别自动禁用，不影响现有自动操作。

## 配置说明（config.json）

### window

- `title_keyword`：目标窗口标题包含的关键字（必填，可用 `--title` 临时覆盖）
- `class_keyword`：可选的窗口类名过滤
- `auto_activate`：开始时自动把目标窗口切到前台

### capture

- `backend`：`auto` / `dxcam` / `gdi` / `mss`（`auto` 依次尝试 dxcam、gdi、mss；gdi 不带 `CAPTUREBLT`，避免高频截屏导致鼠标光标闪烁）
- `fps`：截屏线程目标帧率
- `display_scale`：调试窗口显示缩放（1.0 为原始大小）

### perception

- `gameplay_roi`：目标搜索区域（相对坐标），排除 UI 区域可提速
- `enemy_threshold`：模板匹配阈值（0~1），识别不到目标就降低，误识别就提高
- `player_threshold`：主体模板阈值
- `nms_iou`：重叠框去重阈值

### bars

每个状态条：

- `roi`：相对坐标 `[x, y, w, h]`，由校准工具写入
- `hsv_ranges`：状态条颜色的 HSV 范围列表，默认红色 HP、蓝色 MP。**颜色不对时优先改这里**
- `min_column_ratio`：一列中至少多少比例的像素匹配颜色才算有效列

### skills

- `bar_roi`：快捷栏整体相对坐标
- `slot_rois`：可选，显式指定每个快捷操作格的相对坐标（默认按 `slot_count` 等分）
- `slot_count`：快捷操作格数量
- `slot_gap_ratio`：快捷操作格之间的间距占格子宽度的比例
- `gray_saturation_max`：中心区域平均饱和度低于该值视为冷却中
- `value_ready_min`：平均亮度高于该值也视为就绪（适配白色系图标）
- `center_ratio`：只取格子中心区域计算，避开覆盖数字

### scenes

- `default_threshold`：场景模板默认匹配阈值
- `confirm_frames`：连续多少帧识别到同一场景才确认，防抖
- `poll_interval_s`：场景识别轮询间隔
- `loading.enabled`：是否启用黑屏加载检测
- `loading.max_mean_brightness` / `max_std`：平均亮度和标准差低于阈值判定为加载
- `loading.confirm_frames`：连续多少帧加载画面才确认
- `profiles`：按场景 ID 覆盖阈值、模板列表、ROI、pHash 参考图

### map_graph

- `enabled`：是否启用自动流程跳转（当前仅配置预留）
- `transitions`：跳转列表，每条包含 `from` / `to` / `actions`
- `retries`：失败重试次数
- `require_loading`：是否等待加载画面
- `from_timeout_s` / `scene_timeout_s` / `loading_timeout_s`：各阶段超时
- `require_from_scene`：跳转前是否必须先识别到起始场景
- `click_margin_ratio`：点击安全边距，防止点到窗口外

### combat

- `attack_range_ratio` / `y_tolerance_ratio`：攻击距离和 Y 轴容差，按画面宽高比例
- `x_move_gain` / `y_move_gain`：移动按键时长换算系数，走得不够调大，走过头调小
- `min_move_s` / `max_move_s`：单次移动按键时长上下限
- `attack_gap_s`：两次攻击之间的间隔范围（秒）
- `search_direction`：无目标时优先移动方向
- `search_step_s`：单次寻路按键时长
- `search_segment_s`：换方向的时间区间
- `navigate_jump_key` / `navigate_jump_prob`：寻路时偶尔跳跃
- `basic_attack`：普通攻击键、距离、Y 容差
- `potion`：喝药键、触发血量、内置冷却
- `skills`：快捷操作列表，每个快捷操作包含
  - `key` 按键
  - `slot` 对应快捷栏格序号（从 0 开始；未校准快捷栏时仅依赖内置冷却）
  - `range_ratio` / `y_tolerance_ratio` 释放条件
  - `cooldown_s` 内置冷却
  - `weight` 评分权重（越高越优先，每次决策带 ±15% 随机）
- `danger`：HP 在窗口期内骤降时后撤

### humanize

- `hold_ms`：按键按住时长范围
- `decision_ms`：决策周期范围
- `hesitate_prob` / `hesitate_ms`：随机犹豫概率与时长
- `cooldown_jitter_s`：快捷项冷却的随机附加量
- `aim_jitter_ratio`：目标位置瞄准误差（按画面尺寸比例）

### safety

- `dry_run`：只感知决策、不发送按键
- `require_foreground`：非前台自动暂停
- `start_running`：启动后直接开始（默认需按 F8）
- `countdown_s`：开始前倒计时

### recorder

- `sessions_dir`：会话目录，默认 `sessions`
- `poll_hz`：键鼠采样频率，默认 125
- `record_mouse_move`：是否记录鼠标移动轨迹
- `frame_sample_hz`：录制时保存画面的频率（默认 4 fps，0 为关闭）
- `frame_max_width`：保存画面的最大宽度（默认 480，用于自动断点分析）
- `trim_idle`：录制结束时自动去掉首尾空闲（默认 true；从第一个操作开始、最后一个操作结束）
- `keys`：录制按键白名单，默认字母/数字/方向键等；目标软件自定义键位需要追加

### replay

- `speed`：默认回放速度
- `loops`：默认循环次数，0 表示无限
- `loop_gap_s`：循环之间的间隔（秒），可写单个数字或 `[最小, 最大]`；界面上的“间隔(s)”优先
- `foreground_timeout_s`：等待目标窗口回到前台的超时
- `checkpoint_settle_s`：断点满足条件后的停顿
- `checkpoint_immediate`：断点完成后立即执行下一事件（压缩录制时的空闲间隔）
- `state_poll_interval_s`：`wait_visual` 画面匹配轮询间隔
- `state_confirm_frames`：`wait_visual` 需要连续匹配的帧数
- `checkpoint_timeout_s`：断点等待超时
- `max_duration_s`：单次回放最大时长

### analyzer

- `min_gap_s`：超过该秒数的空闲段才生成断点建议（默认 1.5；“自动加断点”窗口可直接修改）
- `change_threshold`：前后画面差异达到该值才认为发生了状态变化（默认 0.05）
- `baseline_offset_s`：取前一动作后多久的画面作为变化前基准
- `target_lead_s`：取下一动作前多久的画面作为目标状态参考
- `target_window_s`：目标参考画面的搜索时间窗
- `threshold_floor` / `threshold_cap`：自动阈值下限/上限
- `checkpoint_offset_s`：建议断点相对前一动作的偏移

## 调参指南

**识别不到目标**
- 用 `tools/snapshot.py` 截图，确认目标外观与 `templates/enemy/` 中的模板一致（同分辨率、同缩放）
- 一个目标多抓几个动作/朝向的模板
- `enemy_threshold` 从 0.8 调到 0.7 试试
- 缩小 `gameplay_roi` 排除 UI 干扰

**状态条读数不准**
- 校准框要框住**条形填充部分**，不要含外框和背景
- 调整 `bars.hp.hsv_ranges`：先用画图工具取色，或截图后用脚本查看 HSV
- 状态条有渐变/多段颜色时，可加多个 HSV 范围

**快捷操作一直不放 / 一直放**
- 未校准快捷栏时，完全依赖 `cooldown_s`，按实际冷却设置
- 校准后如果图标偏灰，降低 `gray_saturation_max` 或提高 `value_ready_min`
- 快捷操作放空说明距离/容差过大，调小 `range_ratio`

**走位不对齐 / 打空**
- 这是 2D 横版最常见问题：先确认 Y 轴对齐后再出手
- `y_tolerance_ratio` 调小会更严格，但太小会左右摇摆
- `y_move_gain` 决定一次上下移动的时长，建议从 0.35 开始微调

**看起来太机械**
- 提高 `hold_ms`、`decision_ms` 的抖动区间
- 提高 `hesitate_prob` 到 0.05 左右
- 调整 `aim_jitter_ratio`

## 常见问题

**截屏全黑或失败**
- 改为无边框窗口模式；独占全屏通常无法用 Desktop Duplication 捕获
- 关闭目标窗口内 HDR，或确认显示器/缩放设置没变
- 以管理员身份运行终端（部分反作弊/权限场景需要）

**按键没反应**
- 确认目标窗口在前台（脚本默认非前台不发送按键）
- 以管理员身份运行；部分目标程序只接受更高完整性级别的输入
- 少数目标程序需要内核级输入注入（Interception 驱动）或硬件 HID，当前版本使用标准扫描码 SendInput
- 目标窗口内如果改过键位，记得同步改 `config.json`

**图形界面预览不刷新**
- 确认顶部已锁定正确的目标窗口标题
- 独占全屏无法截图，请切换无边框窗口模式
- 查看右侧日志中的暂停原因和截屏错误

**EXE 启动慢或被杀毒软件拦截**
- onefile 打包会在启动时解压到临时目录，首次启动约 3~8 秒属正常
- 未签名程序可能触发 SmartScreen：更多信息 → 仍要运行
- 若被杀毒软件隔离，将程序目录加入白名单后重新打包或运行

**画面尺寸变了**
- 相对坐标会自动适配分辨率，但模板图片是固定像素的，分辨率/缩放变化后需要重新抓模板并校准

## 打包与版本

当前版本号在 `VERSION` 文件中，打包脚本会读取它并输出：

```
自动化控制台_v<版本号>.exe
```

修改代码后：

```powershell
build_exe.bat
```

- 脚本自动安装/升级 `pyinstaller` 与 `pillow`
- 构建版本化 EXE，不会覆盖正在运行的旧版本
- 构建完成写入 `last_build.txt`

更新并启动新版本：

```powershell
start_latest.bat
```

它会关闭所有 `自动化控制台*.exe` 和旧名称 `自动化调试器*.exe` 进程，然后启动根目录下最新的版本化 EXE。常用流程：

1. 修改 `VERSION`（例如 `0.7.0` → `0.7.1`）
2. 运行 `build_exe.bat`
3. 运行 `start_latest.bat`

注意事项：

- EXE 只包含代码和依赖，`config.json`、`templates/`、`sessions/`、`captures/`、`logs/` 都在 EXE 同目录，升级 EXE 不会覆盖它们
- 分发给别人时至少带上最新版本化 EXE、`config.json`；模板和场景需要重新校准或一起打包
- 打包体积约 75 MB（OpenCV + numpy + Tk），启动时解压，属正常现象
- 旧版本可以保留用于回滚；`start_latest.bat` 只启动最新的版本化 EXE

## 扩展方向

- **目标识别升级**：把 `templates/` 换成 YOLOv8 检测模型（训练 300~500 张截图），在 `perception.py` 替换 `TemplateBank.detect`
- **流程循环**：增加出口/传送点识别，清理后自动寻找出口；增加过场检测
- **连招系统**：给快捷操作配置取消窗口和连段序列，替代当前的独立快捷操作评分
- **卡住恢复**：检测长时间无目标/无位移，执行随机脱困动作
- **录像回放**：记录每帧感知结果和动作，离线调参
- **输入兜底**：接入 Interception 驱动或 Arduino HID

## 日志

- 图形界面：`logs/gui_*.log`，右侧日志面板同步显示
- 命令行：`logs/run_*.log`
- 界面未捕获异常：`logs/gui_crash.log`

日志包含截屏后端、窗口信息、暂停原因和识别异常。调参时先看日志，再看调试叠加层。
