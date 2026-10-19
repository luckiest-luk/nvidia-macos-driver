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

v2.3.7 原版遗漏了三份编译期测试输入。发布包及按测试正文匹配的历史恢复未成功，
因此本 fork 补写了以下三份合成 LLVM IR，直接入库，不再从网络下载：

- `translator/translator/validation/fixtures/public/kernel_nullable_branch_cursor.ll`
- `translator/translator/validation/fixtures/public/kernel_nested_loop_constant_weight_walk.ll`
- `translator/translator/validation/fixtures/public/vertex_narrow_attributes.ll`

这些是本 fork 新写的替代输入，不是找回的上游原件。覆盖可为空的分支游标、双层循环中的常量权重游标、
以及 uchar/ushort 顶点属性。Rust 测试正文和原有结构断言保持不变。比较使用原版源码加相同补充输入，
因此不声称复现了上游缺失输入的原始覆盖范围。三项对应测试在基线和当前分支都必须通过，
不能把替代输入造成的失败当作可接受的历史失败。

`tools/restore_translator_fixtures.py` 将这三份入库文件补入基线临时源码；两边同名输入若不同，会停止比较。
附件中的 `restored-fixtures/` 保存文件和记录 `source.json`（明确标记非上游原件及每份 SHA256）。
`userland` 还会调用编译出的翻译器 C 接口，核对原测试要求的缓冲区访问、Phi、窄整数能力和 32 位输入接口，
结果及 SPIR-V 输出保存于 `ui-build-logs-<commit>/fixture-checks/`。这只验证翻译结果结构，不做 GPU 像素验证。

无需 Rust 编译即可对已有的 x86_64 翻译器库执行同样检查：

```sh
python3 tools/check_translator_fixtures.py --library /path/to/libnvmtl_translate.dylib --output .local/fixture-checks
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
