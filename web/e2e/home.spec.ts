import { expect, test } from "@playwright/test";

test("home page shows the product promise", async ({ page }) => {
  await page.goto("/");

  await expect(page).toHaveTitle(/AlquilerApp/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(
    "Cobra a tiempo sin perseguir a nadie",
  );
  await expect(page.locator("html")).toHaveAttribute("lang", "es-DO");
});
