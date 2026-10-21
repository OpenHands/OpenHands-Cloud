import { expect, test } from "@playwright/test";
import { existsSync, readFileSync } from "fs";
import path from "path";

import { ROLE_ONLY_SPECS, type RunUser } from "../utils/config";

const testsDir = path.resolve(import.meta.dirname, "../tests");
const roles = Object.keys(ROLE_ONLY_SPECS) as RunUser[];

test("no spec is limited to both roles, which would schedule it nowhere", () => {
  const [returning, newUser] = roles.map((role) => ROLE_ONLY_SPECS[role]);
  expect(returning.filter((file) => newUser.includes(file))).toEqual([]);
});

for (const role of roles) {
  for (const file of ROLE_ONLY_SPECS[role]) {
    test(`${file} exists and guards itself to the ${role} role`, () => {
      const specPath = path.join(testsDir, file);
      expect(existsSync(specPath)).toBe(true);
      expect(readFileSync(specPath, "utf8")).toMatch(
        new RegExp(`runUser\\(testInfo\\) !== "${role}"`),
      );
    });
  }
}
