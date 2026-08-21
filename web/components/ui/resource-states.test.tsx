import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";

import { ResourceBlock } from "@/components/ui/resource-states";
import { recordedResource } from "@/lib/fixtures/movie-fixtures";

it("keeps a healthy resource visible when a sibling resource fails", async () => {
  const healthy = recordedResource("catalog", ["Catalog is here"]);
  const failed = recordedResource("evidence", ["Evidence"], ["evidence"]);
  const { container } = render(
    <main>
      <ResourceBlock label="Catalog" result={healthy}>{(items) => <p>{items[0]}</p>}</ResourceBlock>
      <ResourceBlock label="Evidence" result={failed}>{(items) => <p>{items[0]}</p>}</ResourceBlock>
    </main>,
  );

  expect(screen.getByText("Catalog is here")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("Evidence is taking a night off");
  expect(await axe(container)).toHaveNoViolations();
});
