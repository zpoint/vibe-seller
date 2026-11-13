---
description: 'Connect Vibe Seller to WorkBuddy: paste the JSON from the dialog into the connector editor and trust it, or write it into mcp.json — then check it works, change or remove it.'
---

# Connect Vibe Seller to WorkBuddy

Once it's connected, you can ask WorkBuddy to create Vibe Seller tasks, follow
their progress and answer their questions.

Two things to get ready first:

1. An API key — see [First: create an API key](mcp.md#first-create-an-api-key).
   Right after you create it, the dialog shows your server address and a
   ready-made WorkBuddy config;
2. WorkBuddy installed.

## Connect

=== "In the app"

    1. Open **Experts · Skills · Connectors** in the left column and switch
       to the **Connectors** tab.

        ![The Connectors tab in the left column](images/mcp_workbuddy_win_01_sidebar_connectors_en.png)

    2. In the **Custom connectors** area, click **+ Add MCP** under
       **MCP Server Management** to open the editor.

        ![The Custom connectors area](images/mcp_workbuddy_win_02_connectors_custom_en.png)

        ![+ Add MCP under MCP Server Management](images/mcp_workbuddy_win_03_mcp_management_add_en.png)

    3. Copy the JSON from the dialog's **In the app** tab, paste it into the
       editor and click **Save**. The editor starts with an empty
       `{"mcpServers": {}}`, so `Ctrl+A` then `Ctrl+V` works.

        ![Pasting the config, then Save](images/mcp_workbuddy_win_04_paste_json_save_en.png)

        !!! warning "If the editor already lists other servers, don't select-all-replace"
            Selecting all and replacing would delete those servers too. Add
            just the `"vibe-seller": {…}` entry inside the `mcpServers`
            braces, then save.

    4. Back in the list, click **Trust** on `vibe-seller`. It enables and the
       list reads `32/32 tools enabled`.

        ![Click Trust](images/mcp_workbuddy_win_06_list_trust_en.png)

        ![Trusted: 32/32 tools enabled](images/mcp_workbuddy_win_07_trusted_enabled_en.png)

=== "Command line / config file"

    The file is `~/.workbuddy/mcp.json` — on Windows
    `%USERPROFILE%\.workbuddy\mcp.json`. Write the JSON from the dialog's
    **In the app** tab into it, save, reopen WorkBuddy and click **Trust** in
    the list. The block looks like this:

    ```json
    {
      "mcpServers": {
        "vibe-seller": {
          "url": "http://localhost:7777/api/mcp",
          "headers": {
            "Authorization": "Bearer vst_…"
          }
        }
      }
    }
    ```

## Check it works

Start a new chat and ask “how many stores are in vibe-seller right now?”. If it
names your stores, you're connected.

![The answer, with the store count](images/mcp_workbuddy_win_08_verify_answer_en.png)

## Change or remove

To pause it, flip the switch off in the list. To remove it, click **+ Add MCP**
again, delete the `"vibe-seller": {…}` entry in the editor and save.

![Deleting the vibe-seller entry in the editor](images/mcp_workbuddy_win_10_edit_to_remove_en.png)

![After removal, vibe-seller is gone from the list](images/mcp_workbuddy_win_11_after_remove_en.png)

## Next

See [What you can ask](mcp.md#what-you-can-ask) for what to say once you're
connected, and [Examples](examples.md) for full walkthroughs.

![Demo: driving Vibe Seller from WorkBuddy — waits sped up](images/mcp_workbuddy_demo_en.gif)
