"""
Authentication Setup Helper
Interactive script to save browser authentication state for automated captures
"""

import asyncio
import json
import sys
import re
from pathlib import Path
from urllib.parse import urlparse

# Add parent directory to Python path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from playwright.async_api import async_playwright


class Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RED = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_success(msg: str):
    print(f"{Colors.GREEN}✓ {msg}{Colors.END}")


def print_info(msg: str):
    print(f"{Colors.BLUE}ℹ {msg}{Colors.END}")


def print_warning(msg: str):
    print(f"{Colors.YELLOW}⚠ {msg}{Colors.END}")


def print_error(msg: str):
    print(f"{Colors.RED}✗ {msg}{Colors.END}")


# Predefined app configurations
APP_CONFIGS = {
    'linear': {
        'name': 'Linear',
        'url': 'https://linear.app/login',
        'domain': 'linear.app'
    },
    'notion': {
        'name': 'Notion',
        'url': 'https://www.notion.so/login',
        'domain': 'www.notion.so'
    },
    'github': {
        'name': 'GitHub',
        'url': 'https://github.com/login',
        'domain': 'github.com'
    },
    'asana': {
        'name': 'Asana',
        'url': 'https://app.asana.com/login',
        'domain': 'app.asana.com'
    },
}


def sanitize_domain(domain_input: str) -> str:
    """
    Sanitize domain input to create valid filename.
    Extracts hostname from URL if needed.
    """
    # If it looks like a URL, parse it
    if domain_input.startswith(('http://', 'https://')):
        parsed = urlparse(domain_input)
        domain = parsed.netloc or parsed.path
    else:
        domain = domain_input
    
    # Remove any remaining invalid characters
    domain = re.sub(r'[<>:"/\\|?*]', '', domain)
    domain = domain.strip()
    
    # Remove trailing slashes
    domain = domain.rstrip('/')
    
    # Replace spaces with underscores
    domain = domain.replace(' ', '_')
    
    return domain


async def save_auth_state(app_name: str, login_url: str, domain: str, output_dir: Path):
    """
    Interactive browser session to save authentication state
    
    Args:
        app_name: Display name of the app
        login_url: URL to start login process
        domain: Domain name for the auth file
        output_dir: Directory to save auth state
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Sanitize domain for filename
    safe_domain = sanitize_domain(domain)
    
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}Authentication Setup for {app_name.upper()}{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    print_info("A browser window will open.")
    print_info("Please log in to your account manually.")
    print_info("Once logged in and on the main page, return here.")
    print_warning("Do NOT close the browser window!")
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}\n")
    
    input(f"{Colors.BOLD}Press Enter to open browser...{Colors.END}")
    
    async with async_playwright() as p:
        print_info("Launching browser...")
        
        try:
            browser = await p.chromium.launch(
                headless=False,
                slow_mo=100  # Slightly slow for better visibility
            )
            
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale='en-US',
            )
            
            page = await context.new_page()
            
            print_success(f"Browser opened. Navigating to {login_url}...")
            
            # Navigate with increased timeout and better error handling
            try:
                await page.goto(login_url, wait_until='domcontentloaded', timeout=60000)
                print_success("Page loaded successfully!")
            except Exception as e:
                print_warning(f"Initial load had issues: {e}")
                print_info("But browser is open, you can still log in manually")
            
            # Wait a bit for page to settle
            await page.wait_for_timeout(2000)
            
            print(f"\n{Colors.BOLD}{Colors.YELLOW}{'='*60}{Colors.END}")
            print(f"{Colors.BOLD}{Colors.YELLOW}ACTION REQUIRED:{Colors.END}")
            print(f"{Colors.YELLOW}1. Complete the login process in the browser{Colors.END}")
            print(f"{Colors.YELLOW}2. Navigate to your workspace/dashboard{Colors.END}")
            print(f"{Colors.YELLOW}3. Make sure you're on a page AFTER login{Colors.END}")
            print(f"{Colors.YELLOW}4. Return here and press Enter{Colors.END}")
            print(f"{Colors.BOLD}{Colors.YELLOW}{'='*60}{Colors.END}\n")
            
            # Wait for user to complete login
            input(f"{Colors.BOLD}Press Enter after you've logged in and reached your workspace...{Colors.END}")
            
            print_info("Saving authentication state...")
            
            # Get current URL to verify login
            try:
                current_url = page.url
                print_info(f"Current URL: {current_url}")
                
                # Check if still on login page
                if 'login' in current_url.lower() or 'signin' in current_url.lower() or 'auth' in current_url.lower():
                    print_warning("You appear to still be on a login/auth page.")
                    print_info("Ideally, navigate to your workspace/dashboard first.")
                    response = input("Continue anyway? (y/n): ").lower()
                    if response != 'y':
                        print_error("Authentication setup cancelled")
                        await browser.close()
                        return False
            except Exception as e:
                print_warning(f"Could not verify URL: {e}")
            
            # Save storage state
            try:
                storage_state = await context.storage_state()
                
                # Save to file with sanitized filename
                output_file = output_dir / f"{safe_domain}.json"
                
                print_info(f"Saving to: {output_file}")
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(storage_state, f, indent=2)
                
                print_success(f"Authentication state saved!")
                
                # Show summary
                cookies_count = len(storage_state.get('cookies', []))
                origins_count = len(storage_state.get('origins', []))
                
                print(f"\n{Colors.BOLD}Summary:{Colors.END}")
                print(f"  Cookies saved: {cookies_count}")
                print(f"  Origins saved: {origins_count}")
                print(f"  File: {output_file.name}")
                print(f"  Domain: {safe_domain}")
                
                if cookies_count == 0:
                    print_warning("\n⚠ No cookies saved! You may not have been logged in.")
                else:
                    print_success(f"\n✓ Successfully saved {cookies_count} cookie(s)")
                
                print_warning("\nKeep this file secure! It contains your login session.")
                print_info("This file will be used for automated workflow captures.\n")
                
            except Exception as e:
                print_error(f"Failed to save auth state: {e}")
                await browser.close()
                return False
            
            await browser.close()
            return True
            
        except Exception as e:
            print_error(f"Error during browser session: {e}")
            return False


def list_apps():
    """Display available app configurations"""
    print(f"\n{Colors.BOLD}Available Apps:{Colors.END}\n")
    for key, config in APP_CONFIGS.items():
        print(f"  {Colors.BLUE}{key:12}{Colors.END} - {config['name']}")
    print(f"\n  {Colors.BLUE}{'custom':12}{Colors.END} - Custom app (you provide URL and domain)")
    print()


async def interactive_setup():
    """Interactive setup with menu"""
    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.BOLD}UI Capture System - Authentication Setup{Colors.END}")
    print(f"{Colors.BOLD}{'='*60}{Colors.END}")
    
    list_apps()
    
    # Get app selection
    app_key = input(f"{Colors.BOLD}Select app (or 'quit' to exit): {Colors.END}").strip().lower()
    
    if app_key == 'quit':
        print_info("Setup cancelled")
        return
    
    # Determine output directory
    output_dir = PROJECT_ROOT / 'storage_state'
    
    if app_key in APP_CONFIGS:
        config = APP_CONFIGS[app_key]
        success = await save_auth_state(
            app_name=config['name'],
            login_url=config['url'],
            domain=config['domain'],
            output_dir=output_dir
        )
    elif app_key == 'custom':
        print(f"\n{Colors.BOLD}Custom App Setup{Colors.END}\n")
        print_info("Tips:")
        print_info("  - App name: Just a display name (e.g., 'My Portfolio')")
        print_info("  - Login URL: The full URL (e.g., 'https://example.com/login')")
        print_info("  - Domain: Just the hostname (e.g., 'example.com' or 'app.example.com')")
        print()
        
        app_name = input("App name: ").strip()
        login_url = input("Login URL: ").strip()
        domain_input = input("Domain (just hostname, not full URL): ").strip()
        
        if not all([app_name, login_url, domain_input]):
            print_error("All fields are required")
            return
        
        # Validate and clean domain
        domain = sanitize_domain(domain_input)
        
        if domain != domain_input:
            print_warning(f"Domain sanitized from '{domain_input}' to '{domain}'")
            confirm = input("Is this correct? (y/n): ").lower()
            if confirm != 'y':
                print_info("Setup cancelled. Please enter just the hostname (e.g., 'github.com')")
                return
        
        success = await save_auth_state(
            app_name=app_name,
            login_url=login_url,
            domain=domain,
            output_dir=output_dir
        )
    else:
        print_error(f"Unknown app: {app_key}")
        print_info("Run again and select from the available options")
        return
    
    if success:
        print_success("\nAuthentication setup completed successfully!")
        
        # Ask if they want to set up another
        another = input(f"\n{Colors.BOLD}Set up another app? (y/n): {Colors.END}").lower()
        if another == 'y':
            await interactive_setup()


async def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Save authentication state for web apps',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (recommended)
  python setup_auth.py
  
  # Direct mode
  python setup_auth.py --app linear --url https://linear.app/login --domain linear.app
  
  # Custom app
  python setup_auth.py --app "My App" --url https://myapp.com/login --domain myapp.com
        """
    )
    
    parser.add_argument('--app', help='App name')
    parser.add_argument('--url', help='Login URL')
    parser.add_argument('--domain', help='Domain name (e.g., linear.app)')
    parser.add_argument('--output-dir', type=Path, default=PROJECT_ROOT / 'storage_state',
                       help='Output directory for auth files')
    parser.add_argument('--interactive', action='store_true',
                       help='Force interactive mode')
    
    args = parser.parse_args()
    
    # Interactive mode
    if args.interactive or not all([args.app, args.url, args.domain]):
        await interactive_setup()
    else:
        # Direct mode - sanitize domain
        domain = sanitize_domain(args.domain)
        await save_auth_state(
            app_name=args.app,
            login_url=args.url,
            domain=domain,
            output_dir=args.output_dir
        )


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Setup interrupted by user{Colors.END}")
        sys.exit(0)
    except Exception as e:
        print_error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)