---
description: Let the AI use Google Workspace — click Connect in Vibe Seller, create a Google sign-in key once in Google Cloud, sign in with your Google account, and the AI reads and sends Gmail, edits Sheets and Docs, uploads to Drive and adds Calendar events. Includes the FAQ.
---

# Google Workspace

Once connected, the AI works in your Google account for you: it reads and
sends Gmail, reads and edits Google Sheets and Docs, uploads files to
Google Drive and adds Calendar events. You install nothing yourself:
Vibe Seller downloads Google's official Google Workspace program and
keeps it on the latest version every day.

The whole Vibe Seller connects **one** Google account, and every task on
the team uses it, so **a dedicated work account is best**. Only an admin
can connect, sign in again or disable it.

## Connect it

You need a Google account and a Vibe Seller admin account. The first time
takes about 10 minutes; step 2 is done only once.

### 1. Click Connect

Open **Settings → Integrations** in Vibe Seller and click **Connect**
under "Google Workspace". The first time, Vibe Seller downloads Google's
official program (about 6 MB), usually in seconds.

![The Connect button on the Google Workspace row](images/gws_connect_en.png)

### 2. Create your Google sign-in key (once)

Google needs a sign-in key from your own Google Cloud project before
anything can sign in to your account. It is free and needs no credit
card. Settings lists every step, each with an **Open** link to the right
page in Google Cloud:

![One-time setup: six steps and the "Choose the downloaded file" button](images/gws_setup_en.png)

Open every link **with the Google account you want to connect**.

**2.1 Create a project.** Any name, for example "vibe-seller"; click
**Create**. Then click the project name at the top left and make sure
the new project is selected: if you have used Google Cloud before, it
sometimes stays on an earlier project, and the next steps would change
that one instead.

![New project named vibe-seller; click Create](images/gcp_1_project.png)

**2.2 Turn on the five Google services** (Gmail, Drive, Sheets, Docs,
Calendar). Check that the page names your new project, click **Next**,
then **Enable**, and wait a few seconds for the confirmation.

![Confirm the project, click Next](images/gcp_2a_confirm.png)

![The five services; click Enable](images/gcp_2b_enable.png)

**2.3 Set up Google Auth Platform.** Click **Get started**, then fill in:

![Google Auth Platform not configured yet; click Get started](images/gcp_3a_getstarted.png)

- App name: anything, for example "Vibe Seller" (Google shows it when you
  sign in); User support email: pick your email from the list. Click
  **Next**.

    ![App name and user support email](images/gcp_3b_appinfo.png)

- Audience: choose **External**, click **Next**.

    ![Audience: External](images/gcp_3c_external.png)

- Contact information: type your email and **press Enter** — it turns
  into a small tag — then click **Next**.

    ![The contact email as a tag after Enter (email blurred)](images/gcp_3d_contact.png)

- Tick the agreement, click **Continue**, then **Create**.

    ![Tick the agreement, click Continue, then Create](images/gcp_3e_finish.png)

**2.4 Add your account as a test user.** In the left menu open
**Audience**, find **Test users**, click **Add users**, type the Google
account you want to connect, **press Enter** and click **Save**. Check
that the account now shows in the test users list; if it doesn't, it
wasn't saved — add it again.

![Add users under Test users](images/gcp_4a_testusers.png)

![Type the account, press Enter, click Save (email blurred)](images/gcp_4b_adduser.png)

**2.5 Create the client and download the key file.** In the left menu
open **Clients**, click **Create client**, choose **Desktop app** as the
application type (any name) and click **Create**.

![Application type Desktop app; click Create](images/gcp_5b_desktop.png)

In the window that opens, **click Download JSON right away**: once the
window is closed, Google no longer lets you download that file, and you
would have to create a new client.

![OAuth client created; click Download JSON (client ID and secret blurred)](images/gcp_5c_download.png)

**2.6 Choose the file.** Back in Vibe Seller, click **Choose the
downloaded file** and pick the file you just downloaded (its name starts
with "client_secret").

### 3. Sign in with Google

A Google sign-in link appears in Settings. Click it, and on the page that
opens:

1. Choose the Google account to connect.

    ![Choose the account (account blurred)](images/gws_google_choose.png)

2. Google says "Google hasn't verified this app": that is the app you
   created in step 2. Click **Continue**.

    ![Google hasn't verified this app; click Continue](images/gws_google_unverified.png)

3. Click **Continue** again.

    ![Sign in to Vibe Seller; click Continue (account blurred)](images/gws_google_signin.png)

4. Tick **Select all** and click **Continue**. A service left unticked is
   one the AI can't use.

    ![Tick Select all, click Continue](images/gws_google_scopes.png)

5. Once you see "Success!", close that page.

    ![Signed in](images/gws_google_success.png)

![The sign-in link (red box) and the address box below it (shown here during "Sign in again"; account blurred)](images/gws_signin_en.png)

### 4. Done

Back in Settings, the Google Workspace row shows "Signed in. Finishing the
setup…", then "Signed in as … · The AI can use it" a few seconds later.

![Signed in; the AI can use it (account blurred)](images/gws_done_en.png)

!!! tip "Opened Vibe Seller from another computer?"

    After you sign in, Google sends the browser back to an address on
    **the computer that runs Vibe Seller**. If you opened Settings on
    another computer (your laptop, say), that browser shows "This site
    can't be reached" after the last **Continue**. That is expected: copy
    the **whole address** from its address bar (it starts with
    `http://localhost:`), paste it into the box under the sign-in link and
    click **Send**. It works exactly as if you had signed in on that
    computer.

    ![The box for the address](images/gws_paste_en.png)

## Try it

Create a task in any store, for example "Read my Google Doc 'New listing
plan', check my calendar this week for anything about WIDGET-006, then
create a Google Doc 'WIDGET-006 launch checklist' listing what to do each
day, and send me the link." The AI sends you the link to the new Doc when
it is done:

![The Google Doc the AI created: a day-by-day launch checklist (demo data)](images/gws_demo_doc.png)

## What the AI can do

The AI acts as the Google account you connected, following Google's
official skills:

- **Gmail**: read mail, sort the inbox, reply, forward, send new mail.
- **Google Drive**: find, download and upload files, organize folders.
- **Google Sheets / Docs**: read, create and edit.
- **Google Calendar**: see events and create new ones.

The AI only sends mail, shares files or changes documents when the task
asks for it. Other Google services (Chat, Classroom…) are not authorized,
so the AI cannot use them.

## FAQ

<details markdown="1" id="access-blocked"><summary><b>Google says "Access blocked"?</b></summary>

The Google account you signed in with is not a test user of your app, or
step 2.4 didn't save. In Google Cloud, open **Audience** in the left menu
and check the account is in the **Test users** list, then click
**Connect** in Settings (or **Sign in again** if it is connected).
</details>

<details markdown="1" id="sign-in-expired"><summary><b>The row says "Sign-in expired"?</b></summary>

![Sign-in expired; click Sign in again](images/gws_expired_en.png)

The AI can't use Google Workspace until you click **Sign in again** and
repeat step 3 — about a minute.

The usual cause: the app you created in step 2 is in "Testing", and
Google ends sign-ins to apps in Testing after 7 days. Removing Vibe
Seller's access under your Google Account's third-party connections does
the same.
</details>

<details markdown="1" id="key-for-a-website"><summary><b>"That key is for a website"?</b></summary>

The client was created as a **Web application**. In Google Cloud, open
**Clients**, create a new client with application type **Desktop app**,
download its JSON from the window that opens and choose that file.
</details>

<details markdown="1"><summary><b>Closed the window before downloading the JSON?</b></summary>

Google offers the download only in the window shown when the client is
created. Under **Clients**, create another **Desktop app** client and
click **Download JSON** in its window this time. You can delete the
unused client.
</details>

<details markdown="1"><summary><b>The AI says a Google service is not enabled?</b></summary>

That service was missed in step 2.2, or the steps after 2.1 went to
another project. Open the step-2 link in Settings again, check that the
page names your project and that all five services are enabled; it takes
a few minutes, and you don't need to sign in again.
</details>

<details markdown="1"><summary><b>Use another Google account, or another sign-in key?</b></summary>

Another account: click **Sign in again** on the Google Workspace row and
sign in with it (add it as a test user first). Another key: click
**Change sign-in key**, choose the new file, then sign in once more.
</details>

<details markdown="1"><summary><b>I installed gws myself and signed in before. Is that still used?</b></summary>

The earlier Google Workspace switch in Settings → Integrations used the
gws tool you installed and signed in to yourself. Now Vibe Seller uses
its own copy of the official program with its own sign-in. It **never
reads or changes** your own copy or its sign-in, and the AI no longer
uses it. If that switch was on before you upgraded, the Google
Workspace row asks you to connect once more; follow the steps above.
Your own copy keeps working in your terminal as before.
</details>

<details markdown="1"><summary><b>Does this computer need to reach Google?</b></summary>

Yes. Downloading the official program, signing in and the AI's use of
Google services all need the computer running Vibe Seller to reach
GitHub and Google. If you normally need a proxy for that, turn it on
first.
</details>
