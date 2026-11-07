import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Department, Group, Page, Role, User } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export type PrincipalType = "user" | "group" | "role" | "department";

export const PRINCIPAL_LABELS: Record<PrincipalType, string> = {
  user: "Person",
  group: "Group",
  role: "Role",
  department: "Department",
};

/** Lists of people and groups for pickers. They are only fetched when the caller may see them. */
export function usePeople() {
  const { can } = useAuth();
  const people = can("users.manage") || can("groups.manage");
  const users = useQuery({
    queryKey: ["people", "users"],
    enabled: people,
    queryFn: () => api.get<Page<User>>("/users", { query: { limit: 200 } }),
  });
  const groups = useQuery({
    queryKey: ["people", "groups"],
    enabled: people,
    queryFn: () => api.get<Page<Group>>("/groups", { query: { limit: 200 } }),
  });
  const roles = useQuery({
    queryKey: ["people", "roles"],
    enabled: people || can("security.admin"),
    queryFn: () => api.get<Page<Role>>("/roles", { query: { limit: 200 } }),
  });
  const departments = useQuery({
    queryKey: ["people", "departments"],
    enabled: people || can("departments.manage"),
    queryFn: () => api.get<Page<Department>>("/departments", { query: { limit: 200 } }),
  });

  function options(type: PrincipalType): { value: string; label: string }[] {
    switch (type) {
      case "user":
        return (users.data?.items ?? []).map((u) => ({ value: u.id, label: `${u.display_name} (${u.email})` }));
      case "group":
        return (groups.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }));
      case "role":
        return (roles.data?.items ?? []).map((r) => ({ value: r.id, label: r.name }));
      case "department":
        return (departments.data?.items ?? []).map((d) => ({ value: d.id, label: d.name }));
    }
  }

  function label(type: string, id: string): string {
    const found = options(type as PrincipalType).find((option) => option.value === id);
    return found?.label ?? `${id.slice(0, 8)}…`;
  }

  return { enabled: people, users, groups, roles, departments, options, label };
}
