# 在自己的 fork 编译 CoreImage 和 #187

工作流为 `.github/workflows/ui-userland.yml`，使用 Intel macOS 15 runner，生成 x86_64、最低 macOS 15.0 的两个用户态文件：

- `NVMTLDriver`：包含本分支的 CoreImage 修改。
- `libnvmtl_translate.dylib`：包含本分支的 #187 修改。

构建和翻译器测试分别运行，可以并行进行。Cargo 依赖与编译结果按 Rust 版本、锁文件、构建脚本及提交缓存。
首次运行仍需完整编译，之后可以复用缓存；云端速度取决于 runner 和缓存命中情况。

## 运行

将整合分支与工作流提交并推送到自己的 fork 后，推送会触发构建。
手动按钮要求工作流已存在于默认分支；在此之前可继续推送该整合分支触发。

在 fork 的 Actions 页面选择 `UI user-space build`。查看两个任务：

1. `userland`：编译插件、翻译器，检查架构、最低系统版本和临时签名，生成 SHA256。
2. `translator-tests`：用同一 Rust 工具链对原版 2.3.7 和当前分支运行已有库测试。原版已存在的失败会保留记录；新增失败、缺失测试或新增忽略会让任务失败。

测试在临时源码副本中进行，工作区的锁文件不变。原版的锁文件需要 Cargo 整理，因此先执行
`cargo update --workspace`，尽量保留已锁定版本，再让两份源码共用整理后的锁文件，执行 `cargo test --locked`。
附件保留 `baseline-original.Cargo.lock`、`resolved.Cargo.lock` 和 `dependencies.log`，便于核对依赖变化。

若测试任务失败，在本次运行的 Summary 页面下方找到 Artifacts，下载 `ui-translator-tests-<commit>`。
其中 `comparison.json` 记录状态，`baseline.log` / `candidate.log` 记录 Cargo 输出；失败命令的最后 100 行也直接显示在任务日志中。
`state: incomplete` 表示比较尚未完成，不能当作测试通过。

v2.3.7 原版有三份未入库的编译期测试输入，比较脚本会自动调用 `tools/restore_translator_fixtures.py` 恢复：

- `translator/translator/validation/fixtures/public/kernel_nullable_branch_cursor.ll`
- `translator/translator/validation/fixtures/public/kernel_nested_loop_constant_weight_walk.ll`
- `translator/translator/validation/fixtures/public/vertex_narrow_attributes.ll`

恢复脚本下载固定版本 `metal2vulkan 0.1.0` 的 crates.io 发布包，用官方索引中的 SHA256 校验，
从 `.cargo_vcs_info.json` 取得发布时的 Git 提交，再从 `steelbrain/metal2vulkan` 的该提交读取真实文件。
发布包本身排除了 `validation/`，因此需要回到其 Git 来源恢复。
若发布时提交尚未包含这些输入，则搜索驱动首次导入翻译器之前的上游历史，要求三项测试函数正文
与驱动所带版本一致，并且三份输入都存在，才接受该历史快照。不会直接使用当前上游的输入，也不生成替代数据。

三份文件补入两边临时源码，工作区不变；已存在的同名文件若内容不同，会停止比较。
附件中的 `restored-fixtures/` 保存文件和来源记录 `source.json`（提交、发布包校验值和每份文件 SHA256），
`fixtures.log` 保存下载与 Git 日志。网络、校验或文件恢复失败会保留 `incomplete`，不会跳过测试。

也可以单独下载到本地工作区，用于后续提交保存这些输入：

```sh
python3 tools/restore_translator_fixtures.py --output translator/translator/validation/fixtures/public
```

在运行页面下载 `ui-userland-x86_64-<commit>` 获取候选文件，另有构建日志与测试结果附件，保留 14 天。
构建成功不代表测试任务成功；使用前同时检查两个任务结果。该流程没有 GPU 运行验证，也不安装或发布驱动。
本地 `.local/` 中的会话历史、日志和依赖不参与上传。

## #273 的限制

本工作流不生成 NVRM。#273 虽已合入源码，仍缺少 Darwin vendor archive、记录的编译参数和相应链接环境。
下载的两个文件只包含 CoreImage 与 #187，不能当作包含三项修改的完整驱动安装包。

## 本地复用

已有 Rust、Xcode Command Line Tools 和 Vulkan-Headers 时，可以运行：

```sh
VULKAN_INCLUDE=/path/to/Vulkan-Headers/include bash build/build_userland_ci.sh
python3 tools/compare_translator_ci.py
```

默认输出与缓存均位于当前仓库 `.local/cloud-ci/`。不使用 EFI，也不需要作者的 SSH 构建主机。
