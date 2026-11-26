---
description: 让 AI 使用 Google Workspace：在 Vibe Seller 里点「开通」，按步骤在 Google Cloud 创建一次登录密钥，再用 Google 账号登录，AI 就能收发 Gmail、读写 Google 表格和文档、上传到云端硬盘、安排日历。包括常见问题。
---

# Google Workspace

开通后，AI 直接用你的 Google 账号做事：收发 Gmail、读写 Google 表格
和文档、把文件上传到 Google 云端硬盘、在日历里安排日程。不用自己安装
任何东西：Vibe Seller 会下载 Google 官方的 Google Workspace 程序，
每天自动更新到最新版本。

整台 Vibe Seller 只连接**一个** Google 账号，团队里所有人的任务都用
它做事，所以**建议用专门的工作账号**。开通、重新登录、停用只有管理员
能操作。

## 开通步骤

需要：一个 Google 账号，以及 Vibe Seller 的管理员账号。第一次大约
10 分钟，其中第 2 步只需要做一次。

### 1. 点「开通」

打开 Vibe Seller「设置 → 集成」，在「Google Workspace」里点「开通」。
第一次会先下载 Google 的官方程序（约 6 MB），一般几秒钟。

![Google Workspace 一行的「开通」按钮](images/gws_connect_zh.png)

### 2. 创建你的 Google 登录密钥（只做一次）

Google 要求先在你自己的 Google Cloud 项目里创建一个登录密钥，才能
登录你的账号。免费，不需要绑定信用卡。设置页上列出了每一步，每一步
后面都有「打开」链接，直接跳到 Google Cloud 里对应的页面：

![一次性设置：六个小步骤和「选择下载的文件」按钮](images/gws_setup_zh.png)

下面每个链接都请**用要连接的 Google 账号**打开。Google Cloud 的界面
可能是中文也可能是英文，下面写成「中文（English）」。

**2.1 新建项目。** 名字随意，例如“vibe-seller”，点「创建（Create）」。
建好后点页面左上角的项目名，确认选中的是这个新项目：如果你以前用过
Google Cloud，它有时会停在之前的项目上，后面几步就会改到别的项目里。

![新建项目，名称填 vibe-seller，点「Create」](images/gcp_1_project.png)

**2.2 开启 5 项 Google 服务**（Gmail、云端硬盘、表格、文档、日历）。
先确认页面上写的是刚建的项目，点「下一步（Next）」，再点
「启用（Enable）」，等几秒提示启用成功。

![确认项目后点「Next」](images/gcp_2a_confirm.png)

![要启用的 5 项服务，点「Enable」](images/gcp_2b_enable.png)

**2.3 设置「Google Auth Platform」。** 打开后点「开始（Get started）」，
按顺序填：

![Google Auth Platform 还没设置，点「Get started」](images/gcp_3a_getstarted.png)

- 应用名称：随意，例如“Vibe Seller”（登录时 Google 会显示这个名字）；
  用户支持邮箱：从下拉框里选你的邮箱。点「下一步（Next）」。

    ![填应用名称，选用户支持邮箱](images/gcp_3b_appinfo.png)

- 目标对象：选「外部（External）」，点「下一步（Next）」。

    ![目标对象选「External」](images/gcp_3c_external.png)

- 联系邮箱：输入你的邮箱后**按回车**，邮箱变成一个小标签才算填好，
  再点「下一步（Next）」。

    ![联系邮箱按回车后变成小标签（邮箱已打码）](images/gcp_3d_contact.png)

- 勾选同意条款，点「继续（Continue）」，最后点「创建（Create）」。

    ![勾选同意条款，点「Continue」，再点「Create」](images/gcp_3e_finish.png)

**2.4 把你的账号加为测试用户。** 在左侧「目标对象」页找到「测试用户」，
点「Add users」（中文界面也显示英文），输入要连接的 Google 账号后
**按回车**，点「保存（Save）」。保存后确认这个账号出现在测试用户列表
里：没出现说明没保存上，再加一次。

![测试用户下的「Add users」](images/gcp_4a_testusers.png)

![输入账号按回车，点「Save」（邮箱已打码）](images/gcp_4b_adduser.png)

**2.5 创建客户端并下载密钥文件。** 在左侧「客户端」页点「创建客户端
（Create client）」，应用类型选「桌面应用（Desktop app）」，名字随意，
点「创建（Create）」。

![应用类型选「Desktop app」，点「Create」](images/gcp_5b_desktop.png)

弹出的窗口里**马上点「下载 JSON（Download JSON）」**：关掉这个窗口后，
Google 不再允许下载这个文件，只能重新创建一个客户端。

![客户端已创建，点「Download JSON」（客户端 ID 和密钥已打码）](images/gcp_5c_download.png)

**2.6 选择文件。** 回到 Vibe Seller，点「选择下载的文件」，选刚才下载
的文件（文件名以“client_secret”开头）。

### 3. 用 Google 账号登录

选好文件后，设置页上出现一个 Google 登录链接。点它，在打开的页面里：

1. 选择要连接的 Google 账号。

    ![选择要连接的账号（账号已打码）](images/gws_google_choose.png)

2. Google 提示「Google 尚未验证此应用（Google hasn't verified this app）」：
   这是你在第 2 步自己创建的应用，点「继续（Continue）」。

    ![「Google hasn't verified this app」，点「Continue」](images/gws_google_unverified.png)

3. 再点一次「继续（Continue）」。

    ![登录 Vibe Seller，点「Continue」（账号已打码）](images/gws_google_signin.png)

4. 勾选「全选（Select all）」，点「继续（Continue）」。没勾选的服务，
   AI 用不了。

    ![勾选「Select all」，点「Continue」](images/gws_google_scopes.png)

5. 看到「Success!」就可以关掉这个页面。

    ![登录成功](images/gws_google_success.png)

![登录链接（红框）和下面的网址粘贴框（图为「重新登录」时的样子，账号已打码）](images/gws_signin_zh.png)

### 4. 开通好了

回到设置页，Google Workspace 那一行先显示「登录成功，正在开通，请稍候…」，
几秒后变成「已登录 · AI 可以使用」，就开通好了。

![已登录，AI 可以使用（账号已打码）](images/gws_done_zh.png)

!!! tip "在另一台电脑上打开 Vibe Seller？"

    Google 登录完成后，会把浏览器带回**运行 Vibe Seller 的那台电脑**
    上的一个本机网址。如果你是在另一台电脑（比如自己的笔记本）上打开
    的设置页，点完最后一个「继续」后，浏览器会显示「无法访问此网站」，
    这是正常的：把浏览器地址栏里的**整个网址**（以 `http://localhost:`
    开头）复制下来，粘贴到登录链接下面的框里，点「提交」，就和在那台
    电脑上登录一样。

    ![粘贴网址的输入框](images/gws_paste_zh.png)

## 开通后试一下

在任意店铺里新建任务，比如「读一下我 Google 文档里的「新品上架计划」，
再看看我日历上这周和 WIDGET-006 有关的安排。然后新建一个 Google 文档
「WIDGET-006 上架清单」，按日期列出每天要做的事，把链接发给我。」
AI 做完会把新文档的链接发给你，打开就是它整理好的清单：

![AI 新建的 Google 文档：按日期整理的上架清单（演示数据）](images/gws_demo_doc.png)

## AI 能做什么

AI 以你连接的 Google 账号做事，按 Google 官方技能的说明工作：

- **Gmail**：读邮件、整理收件箱、回复、转发、发新邮件。
- **Google 云端硬盘**：查找、下载、上传文件，整理文件夹。
- **Google 表格 / 文档**：读取、新建、修改。
- **Google 日历**：查看日程、新建日程。

AI 只在任务要求时发邮件、分享文件或修改文档。其他 Google 服务（Chat、
Classroom 等）没有授权，AI 用不了。

## 常见问题

<details markdown="1" id="access-blocked"><summary><b>Google 提示「已拦截访问权限（Access blocked）」？</b></summary>

你登录的 Google 账号还不是这个应用的测试用户，或者 2.4 步没保存上。
回到 Google Cloud 左侧「目标对象」页，确认这个账号在「测试用户」列表
里，再在设置页点「开通」（已开通的点「重新登录」）。
</details>

<details markdown="1" id="sign-in-expired"><summary><b>那一行显示「登录已失效」？</b></summary>

![登录已失效，点「重新登录」](images/gws_expired_zh.png)

AI 暂时用不了 Google Workspace，点「重新登录」，按第 3 步再登录一次
就好，大约一分钟。

常见原因：你在第 2 步创建的应用处于「测试」状态，Google 对测试状态的
应用，登录 7 天后会自动失效。另外，在 Google 账号的「第三方关联」里
删掉了 Vibe Seller 的访问权限，也会这样。
</details>

<details markdown="1" id="key-for-a-website"><summary><b>选文件时提示「这个密钥是给网站用的」？</b></summary>

创建客户端时应用类型选成了「Web 应用」。在 Google Cloud 左侧「客户端」
页重新创建一个应用类型为「桌面应用」的客户端，在弹出的窗口里下载 JSON，
再选这个文件。
</details>

<details markdown="1"><summary><b>关掉了窗口，没来得及下载 JSON？</b></summary>

Google 只在创建客户端时的那个窗口里提供下载。在左侧「客户端」页再
创建一个「桌面应用」客户端，这次在窗口里点「下载 JSON」。没用的那个
客户端可以删掉。
</details>

<details markdown="1"><summary><b>AI 说某个 Google 服务没有开启？</b></summary>

2.2 步漏开了这项服务，或者 2.1 步之后改到了别的项目。打开设置页里第 2
步的链接，确认页面上是你的项目、5 项服务都已启用；一般几分钟后生效，
不需要重新登录。
</details>

<details markdown="1"><summary><b>想换一个 Google 账号或换一个登录密钥？</b></summary>

换账号：在 Google Workspace 那一行点「重新登录」，用另一个账号登录
（这个账号也要先加为测试用户）。换密钥：点「更换登录密钥」，选新下载
的文件，再登录一次。
</details>

<details markdown="1"><summary><b>以前自己在电脑上装过 gws 并登录过，还有用吗？</b></summary>

以前「设置 → 集成」里的 Google Workspace 开关，用的是你自己在电脑上
安装和登录的 gws 工具。现在 Vibe Seller 用自己下载的官方程序和自己
的登录，**不会读取或改动**你自己装的那一份和它的登录，AI 也不再使用
它。升级后，如果之前开着那个开关，Google Workspace 那一行会提示你
重新开通一次，按上面的步骤操作即可。你自己装的那份照常可以在终端里
使用。
</details>

<details markdown="1"><summary><b>电脑需要能访问 Google 吗？</b></summary>

需要。下载官方程序、登录和 AI 使用 Google 服务，都要运行 Vibe Seller
的电脑能访问 GitHub 和 Google。平时需要代理才能访问的话，请先打开
代理。
</details>
