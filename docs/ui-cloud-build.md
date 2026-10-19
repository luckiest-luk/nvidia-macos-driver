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
