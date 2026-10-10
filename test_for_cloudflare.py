import nodriver as uc

async def main():
    browser = await uc.start()          # apre Chrome vero, finestra visibile
    page = await browser.get("https://www.revolut.com/careers/?text=security")
    await page.sleep(5)                 # lascia il tempo alla verifica di passare
    html = await page.get_content()
    print(html[:500])
    browser.stop()

if __name__ == "__main__":
    uc.loop().run_until_complete(main())