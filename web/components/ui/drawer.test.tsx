import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";

import { Drawer } from "@/components/ui/drawer";

it("opens as a named dialog, closes with Escape, and restores trigger focus", async () => {
  const user = userEvent.setup();
  const { container } = render(
    <Drawer buttonLabel="Why this?" title="Why this movie?">
      <a href="/evidence">Read the evidence</a>
    </Drawer>,
  );
  const trigger = screen.getByRole("button", { name: "Why this?" });

  await user.click(trigger);
  expect(screen.getByRole("dialog", { name: "Why this movie?" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Close Why this movie?" })).toHaveFocus();
  expect(await axe(container)).toHaveNoViolations();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
