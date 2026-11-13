---
description: 在 Qoder 里接入 Vibe Seller：把弹窗给的 JSON 粘贴进添加连接器的 JSON 标签导入，或直接写进 settings.json，以及确认接好、修改和移除。
---

# 在 Qoder 里接入 Vibe Seller

接好之后，在 Qoder 的对话里就能让 Vibe Seller 建任务、跟进进度、回答提问。

需要准备两样东西：

1. 一个 API Key：见[第一步：创建 API Key](mcp.md#第一步创建-api-key)。创建
   成功后弹出的窗口里有你的服务器地址和 Qoder 的现成
   配置；
2. 已经安装 Qoder：从 [qoder.com/download](https://qoder.com/download) 下载
   （国内版的应用名是 **Qoder CN**）。

## 接入

=== "界面操作"

    1. 打开左侧的「扩展」，切换到「连接器」标签。

        ![左侧的「扩展」入口](images/mcp_qoder_win_01_sidebar_extensions_zh.png)

        ![「连接器」标签](images/mcp_qoder_win_02_extensions_connectors_zh.png)

    2. 点右上角「添加连接器」，把对话框切到「JSON」标签。

        ![右上角的「添加连接器」](images/mcp_qoder_win_03_add_connector_zh.png)

        ![对话框切到「JSON」标签](images/mcp_qoder_win_04_dialog_json_tab_zh.png)

    3. 复制弹窗「界面操作」标签里的 JSON，粘贴进去，点「预览导入」。

        ![粘贴配置，准备「预览导入」](images/mcp_qoder_win_05_json_pasted_preview_zh.png)

    4. 确认要导入的条目，点「导入 1 个 MCP」。之前添加过 vibe-seller 的
       话，预览会提示将被替换，确认导入就覆盖旧配置。

        ![「预览导入」的确认界面](images/mcp_qoder_win_06_preview_import_zh.png)

    5. 导入后显示「已安装」（可用 · HTTP），可以直接使用。

        ![导入后 vibe-seller 显示「已安装」](images/mcp_qoder_win_07_installed_ready_zh.png)

=== "命令行 / 配置文件"

    配置写在 `~/.qoder-cn/settings.json`（国内版）或
    `~/.qoder/settings.json`（国际版）的 `mcpServers` 里，保存后重新打开
    Qoder。直接改文件时，改完要在 Qoder 里再点一次「信任」。这一段长这样：

    ```json
    {
      "mcpServers": {
        "vibe-seller": {
          "type": "http",
          "url": "http://localhost:7777/api/mcp",
          "headers": {
            "Authorization": "Bearer vst_…"
          }
        }
      }
    }
    ```

## 确认接好了

新建一个对话，问“现在 vibe-seller 里有几个店铺？”。它把你的店铺列出来，
就接好了。

![对话里答出店铺数](images/mcp_qoder_win_08_verify_answer_zh.png)

## 修改或移除

在「设置」里打开扩展管理，切到「连接器」标签：`vibe-seller` 那一行的「…」
里有「编辑」，改地址或换 Key 都在那里；「删除」→「删除 MCP」确认后即移除。

![「…」菜单里的「删除」](images/mcp_qoder_win_09_menu_delete_zh.png)

![确认删除，点「删除 MCP」](images/mcp_qoder_win_10_delete_confirm_zh.png)

![删除后列表里不再有 vibe-seller](images/mcp_qoder_win_11_after_delete_zh.png)

## 接下来

接入之后怎么用，见[可以这样用](mcp.md#可以这样用)；一步步的完整示例见
[使用示例](examples.md)。

![演示：在 Qoder 里指挥 Vibe Seller（等待过程已加速）](images/mcp_qoder_demo_zh.gif)
