---
description: 登录与账号：登录密码怎么设置和找回、Windows 上为什么没有 vibe-seller 命令。
---

# 登录与账号

## 登录密码怎么设置和找回？

**默认不需要登录。** 全新安装后打开就是主界面——登录是可选功能，只有在「设置 → 账号」里打开「需要登录」才会生效。所以如果你看到登录页，说明这台机器上有人（多半是帮你安装系统的同事）主动开启了登录。

默认账号是 `admin` / `admin`（邮箱 `admin@vibe-seller.local`），**但这套默认值只在密码没有被修改过时有效。** 只要有人在「设置 → 账号」里改过密码，默认值立即失效：密码用 bcrypt 单向哈希保存，无法从数据库里读回来，用户名也可能被改过（你这台机器上的管理员账号不一定还叫 `admin`）。

不确定当前状态时，用一条命令查看：

```bash
vibe-seller reset-auth status
```

它会告诉你：现在是否需要登录、默认密码是否还有效、哪些账号能登录、哪个是默认管理员。

忘了密码就重置。不带参数时命令会交互式询问新密码（输入不显示），重置的是「默认管理员」账号（这个账号后来改成什么名字都算）：

```bash
vibe-seller reset-auth password
```

命令直接修改数据库，服务不需要重启，正在执行的任务不会中断。给其他账号重置时使用 `--user <用户名>`，想同时把默认管理员改名就加上 `--rename`。

需要注意，**重置密码不会让已登录的浏览器下线**：登录依赖浏览器里的 JWT cookie（24 小时滚动有效），重置后旧会话仍然可用。要强制所有人重新登录，需要停止服务，删除 `~/.vibe-seller/data/jwt_secret`，再重新启动。

!!! warning "删除这个文件有连带影响"
    同一份密钥还用于加密你保存进去的紫鸟、邮箱、广告 API 凭据。删除之后那些密码无法解密，需要重新填写一遍；任务的 MCP token 也会失效（任务会自己重新申请）。只是想移除某个人的访问时，更稳妥的做法是修改该账号的密码并开启登录，而不是更换密钥。使用 `JWT_SECRET` 环境变量固定密钥的安装方式，要修改的是那个变量。

**不想要登录页**（应急使用）：

```bash
vibe-seller reset-auth disable    # 关闭登录页
vibe-seller reset-auth enable     # 需要时重新开启
```

关闭后，任何能访问这个端口的人都会以默认管理员身份操作——内网自用没有问题，不要直接暴露到公网。修改后刷新浏览器页面即可生效。

源码安装（clone 下来运行的）没有 `vibe-seller` 命令，请改用 `scripts/reset_auth.sh`（Windows 上是 `scripts\reset_auth.bat`），参数相同；在代码目录里直接执行 `python -m app.auth_reset status` 也可以。

## Windows 上为什么没有 vibe-seller 命令？

因为原生安装包不会把它加入 PATH——在 Windows 上你不需要它：服务由托盘管理（右键图标 → 重启服务 / 退出），登录 Windows 时自动启动，升级走托盘的「检查更新」。

需要在命令行完成操作时（比如上面的 `reset-auth`），可执行文件就在安装目录里，用完整路径调用：

```powershell
& "$env:LOCALAPPDATA\Programs\VibeSeller\.venv\Scripts\vibe-seller.exe" reset-auth status
```

`vibe-seller start`、`stop`、`upgrade` 等命令也都在同一个文件上，只是 Windows 的自动启动和升级由安装包负责，通常用不到。

`vibe-seller` 这个命令是 macOS / Linux（以及 WSL2 里的 Ubuntu）安装方式的入口，那边由 `install.sh` 把它放进 `~/.local/bin`。
