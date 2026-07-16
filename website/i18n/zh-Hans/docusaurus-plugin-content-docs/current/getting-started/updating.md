---
sidebar_position: 3
title: "更新与卸载"
description: "更新、回滚、adopt、eject 或卸载 Hermes"
---

# 更新与卸载

## 更新

```bash
hermes update
```

Hermes 会根据安装方式选择更新路径，不会绕过包管理器修改其管理的安装。

| 安装类型 | `hermes update` 的行为 |
|---|---|
| **托管 bundle**（标准安装器默认） | 下载最新签名发布，验证每个文件，在不可变 slot 中暂存并预检，原子切换 `current.txt`，重新部署 updater、恢复懒加载 feature，并请求重启 gateway。 |
| **源码 checkout**（`install.sh --source`、`install.ps1 -Source` 或 `hermes eject`） | 干净 checkout 原地 fast-forward；存在本地修改时提供新 worktree（默认）、普通 Git merge 或取消。不会自动 stash，也不会原地修改正在使用的 venv。 |
| **Nix、Homebrew、pip 或 Docker** | 拒绝自更新，并提示应使用的包管理器或镜像命令。 |

配置、凭据、会话、skills 等持久状态保留在 `HERMES_HOME`；发布 slot 位于 `$HERMES_HOME/versions/`。

### 查看托管发布状态

```bash
hermes-updater status
hermes-updater status --check
hermes-updater status --check --json
```

状态包含当前/上一个 slot、channel、中断后遗留的 staging、最新可用发布、落后版本数、发布说明及构建 SHA。网络检查失败不会修改当前 slot。

### 原子更新与回滚

托管更新按以下顺序执行：

1. 解析并流式下载当前平台 archive；
2. 强制验证 Ed25519 签名和 manifest 中每个文件的 hash；
3. 解包到 `versions/<version>.staging`；
4. 在 staged slot 中运行 `hermes doctor --preflight`；
5. 将 staging 重命名为不可变 slot，并原子替换 `current.txt`；
6. 更新稳定 launcher/updater、重新应用 feature ledger，并重启服务。

第 5 步之前的任何失败都会删除 staging，当前版本保持不变。立即切回上一个 slot：

```bash
hermes-updater rollback
```

已经运行的进程会继续使用启动时解析到的旧 slot，直到进程重启。

### 源码 checkout 与 worktree

在 Hermes 源码 checkout 内必须明确选择运行环境：

```bash
hermes --dev --version      # 当前 checkout
hermes --global --version   # 已安装/托管的 Hermes
```

为防止环境错配，checkout 内的普通 `hermes` 会拒绝运行。配置 checkout：

```bash
hermes dev sync
hermes dev sync --watch --only tui web
```

如果 `hermes update` 发现本地修改，默认 **Switch** 会创建 `.worktrees/main-<sha>`、完成 provisioning 并重新指向命令链接，同时保持原 checkout 字节级不变。`hermes dev gc` 会删除非活动更新 worktree，但永远不会删除当前活动目标。

### 在托管与源码模式之间切换

旧版干净 checkout 可根据 `updates.adopt: auto|prompt|never` 自动或经提示迁移到托管发布：

```bash
hermes adopt
hermes-updater adopt --undo
```

Adoption 不修改原 checkout，并记录旧命令目标以便撤销。要从托管安装切回开发 checkout：

```bash
hermes eject
```

`eject` 会按当前 slot manifest 中的精确源码 revision 克隆、运行 `hermes dev sync` 并重新指向命令链接。切换过程中继续共享同一个 `HERMES_HOME` 数据。

### 从消息平台更新

发送 `/update`。Gateway 会调用同一 updater，在支持时 drain 活跃任务，并在新 slot 上重启。更新 marker 只在短暂的 flip/restart 临界区存在。

### 包管理器安装

请使用安装所有者：

```bash
# Nix
nix profile upgrade hermes-agent
nix profile rollback

# Homebrew
brew upgrade hermes-agent

# pip（旧版/手动安装）
pip install --upgrade hermes-agent

# Docker：拉取新镜像并重建容器，不要在容器内更新
docker pull nousresearch/hermes-agent:latest
```

## 卸载

```bash
hermes uninstall
```

卸载器可保留 `HERMES_HOME` 供以后重装。若有 gateway 服务，请按提示先停止。由包管理器安装的 Hermes 应通过该包管理器卸载。

手动清理时，删除命令链接和安装目录。只有在确实要擦除配置、凭据、会话、skills 和所有托管 slot 时才删除 `~/.hermes`。
