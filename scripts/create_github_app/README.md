# Description

Create a GitHub App configured for the Replicated self-hosted install of OpenHands Enterprise (OHE).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed
- A GitHub account (you'll need to be signed in)

## Usage

```bash
./create_github_app.py --base-domain <your-domain>
./create_github_app.py --base-domain <your-domain> --org <github-org>
./create_github_app.py --base-domain <your-domain> --callback-port 18080
```

### Options

| Option | Description |
|--------|-------------|
| `--base-domain` | **(Required)** Base domain for your OHE installation (e.g., `mycompany.com`) |
| `--app-name` | Custom name for the GitHub App (default: `openhands-<random>`) |
| `--org` | GitHub organization to create the app in (default: your personal account) |
| `--callback-port` | Local port for the app-creation callback server (default: `9876`) |

### Example

```bash
./create_github_app.py --base-domain mycompany.com
./create_github_app.py --base-domain mycompany.com --org MyCompany
```

## How It Works

1. **A local callback server starts** on port 9876 to capture the OAuth code
2. **Opens your browser** to GitHub's app creation page with a pre-configured manifest
3. **Click "Create GitHub App for \<your-username\>"** to create the app
4. **GitHub redirects back to the local callback server** - the code is captured automatically
5. **Credentials are displayed** and the private key is saved to `./keys/`

No manual copy-paste required - the script automatically captures the authorization code!

If port `9876` is already in use, pass `--callback-port <port>`; the script will fail fast when the callback server cannot start.

### Output

After successful creation, you'll receive:

- **GitHub App ID** - The numeric ID of your app
- **GitHub App Client ID** - Used by the GitHub App authentication flow
- **GitHub App Client Secret** - Keep this secret!
- **GitHub App Webhook Secret** - For webhook verification
- **Private Key** - Saved to `./keys/<app-name>.pem` with owner-only file permissions

The client secret, webhook secret, and private key are sensitive. Keep terminal output private and store the values in the target OHE configuration system immediately.

## Permissions

The created GitHub App requests the following permissions:

| Permission | Access | Description |
|------------|--------|-------------|
| Actions | Write | Manage GitHub Actions workflows |
| Contents | Write | Read and write repository contents |
| Email addresses | Read | Access user email addresses |
| Issues | Write | Manage issues |
| Metadata | Read | Access repository metadata |
| Organization events | Read | View organization activity |
| Pull requests | Write | Manage pull requests |
| Repository webhooks | Write | Manage repository webhooks |
| Commit statuses | Write | Update commit statuses |
| Workflows | Write | Manage workflow files |

## Configuration

The app is configured with:

- **Homepage URL**: `https://app.<base-domain>`
- **Redirect URL** (for app creation): `http://localhost:9876/callback` (local callback server)
- **Callback URL** (for OAuth): `https://auth.app.<base-domain>/realms/allhands/broker/github/endpoint`
- **Webhook URL**: `https://app.<base-domain>/integration/github/events`
- **OAuth on install**: Disabled (Keycloak handles user OAuth at login time)
