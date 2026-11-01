import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";

import ProfileView from "../../src/lib/ProfileView.svelte";
import type { ProjectDocument } from "../../src/lib/contracts";

function doc(meta: Record<string, unknown>): ProjectDocument {
  return { profile: { machine_id: "rig" }, meta } as unknown as ProjectDocument;
}

describe("ProfileView.svelte — the installed machine's project (#722)", () => {
  it("says it is re-synced from the install prefix, not to re-import it", () => {
    render(ProfileView, {
      props: {
        doc: doc({ installed_from: "/opt/anolis", imported_from: "/opt/anolis/projects/rig" }),
      },
    });

    expect(screen.getByTestId("installed-project-advisory")).toHaveTextContent(
      "imports it again from /opt/anolis each time it starts",
    );
    expect(screen.queryByText(/re-import/)).not.toBeInTheDocument();
  });

  it("keeps the re-import advice for a project the user imported", () => {
    render(ProfileView, { props: { doc: doc({ imported_from: "/home/me/rig" }) } });

    expect(screen.queryByTestId("installed-project-advisory")).not.toBeInTheDocument();
    expect(screen.getByText(/re-import/)).toBeInTheDocument();
  });
});
