import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { it, expect } from "vitest";
import { PublicWelcome } from "./PublicWelcome";
import { scenarios } from "./runtime.public";
it("introduces three synthetic scenarios without pretending to provide live operations or a release", () => {
  const html = renderToStaticMarkup(createElement(PublicWelcome, { scenarios, busy: false, error: "", onChoose: () => {} }));
  expect(html).toContain("Try interactive demo");
  expect(html.match(/scenario-card/g)).toHaveLength(3);
  expect(html).toContain("no live analysis");
  expect(html).toContain("Clear does not mean zero risk");
  expect(html).toContain("after it is integrated and published");
  expect(html).not.toContain("Import artifact");
});
