import os, json, unicodedata
from typing import List, Tuple, Dict, Set, Iterable

Pair = Tuple[str, str]

class DSU:
    def __init__(self):
        self.p: Dict[str, str] = {}
        self.sz: Dict[str, int] = {}
    def find(self, x):
        if x not in self.p:
            self.p[x] = x
            self.sz[x] = 1
        if self.p[x] != x:
            self.p[x] = self.find(self.p[x])
        return self.p[x]
    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb: return
        if self.sz[ra] < self.sz[rb]:
            ra, rb = rb, ra
        self.p[rb] = ra
        self.sz[ra] += self.sz[rb]

class ConstraintsStore:
    def __init__(self, scan_folder: str):
        self.json_path = os.path.join(scan_folder, ".constraints.json")
        self.root = os.path.abspath(scan_folder)
        self.version = 1
        self.must_pairs: Set[Tuple[str, str]] = set()
        self.cannot_pairs: Set[Tuple[str, str]] = set()
        self.ignored_files: Set[str] = set()
        self.load_constraints()
    
    # Save and load constraints file
    def load_constraints(self):
        if not os.path.exists(self.json_path):
            return
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.version = data.get("version", 1)

        def _norm_pair(a, b):
            return tuple(sorted([a, b]))

        self.must_pairs.clear()
        for a, b in data.get("must_links", []):
            self.must_pairs.add(_norm_pair(a, b))

        self.cannot_pairs.clear()
        for a, b in data.get("cannot_links", []):
            self.cannot_pairs.add(_norm_pair(a, b))

        self.ignored_files = set(p.lower() for p in data.get("ignored_files", []))

    def save_constraints(self):
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
        data = {
            "version": self.version,
            "root": self.root,
            "must_links": sorted(list(self.must_pairs)),
            "cannot_links": sorted(list(self.cannot_pairs)),
            "ignored_files": sorted(list(self.ignored_files)),
        }
        tmp = self.json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.json_path)

    # add operations
    def add_must_link(self, paths: List[str]):
        uniq = sorted(set(paths))
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                pair = tuple(sorted([a, b]))
                if pair in self.cannot_pairs:
                    continue
                self.must_pairs.add(pair)

    def add_cannot_link(self, a: str, b: str):
        pair = tuple(sorted([a, b]))
        if pair in self.must_pairs:
            return
        self.cannot_pairs.add(pair)

    def add_ignore_files(self, paths: List[str]):
        for p in paths:
            self.ignored_files.add(p.lower())

    def remove_ignore_files(self, paths: List[str]):
        for p in paths:
            self.ignored_files.discard(p.lower())

    def is_file_ignored(self, paths: str) -> bool:
        return paths.lower() in self.ignored_files

    def must_link_groups_only(self, grps: List[str]) -> List[List[str]]:
        s = set(grps)
        dsu = DSU()
        for u in s: dsu.find(u)
        for a, b in self.must_pairs:
            if a in s and b in s:
                dsu.union(a, b)
        groups: Dict[str, List[str]] = {}
        for u in s:
            r = dsu.find(u)
            groups.setdefault(r, []).append(u)
        return [sorted(g) for g in groups.values() if len(g) >= 2]

    # Apply to a group
    def apply_to_group(self, grp: List[str]):
        members = [m for m in grp if not self.is_file_ignored(m)]
        ignored_any = (len(members) != len(grp))
        if len(members) == 0:
            return [], 'ignored'

        used: Set[str] = set()
        subgroups: List[List[str]] = []

        # 1 must-link
        ml_groups = self.must_link_groups_only(members)
        for g in ml_groups:
            subgroups.append(g)
            used.update(g)

        # 2 cannot-link
        cannot_hit = False
        to_remove: Set[str] = set()
        for m in members:
            if m in used:
                continue
            others = [o for o in members if o != m and o not in used]
            if others and all(tuple(sorted([m, o])) in self.cannot_pairs for o in others):
                to_remove.add(m)
        if to_remove:
            cannot_hit = True
            used.update(to_remove)

        # 3 Others, append only when len >=2
        residual = [m for m in members if m not in used]
        if len(residual) >= 2:
            subgroups.append(residual)

        # 4) Filter single image
        subgroups = [g for g in subgroups if len(g) >= 2]

        if not subgroups:
            return [], 'resolved'  # Don't display single image
        
        changed = ignored_any or (len(ml_groups) > 0) or cannot_hit
        return subgroups, ('changed' if changed else 'unchanged')

    # Apply to all groups
    def apply_to_all_groups(self, raw_groups: List[List[str]]):
        view_groups: List[List[str]] = []
        ignored = 0
        resolved = 0
        changed = 0

        for grp in raw_groups:
            subgroups, status = self.apply_to_group(grp)
            if status == 'ignored':
                ignored += 1
                continue
            if status == 'resolved':
                resolved += 1
                continue
            if status == 'changed':
                changed += 1
            view_groups.extend(subgroups)
        
        summary = {
            "total_raw": len(raw_groups),                  # Original groups
            "ignored": ignored,                            # Mark ignore images
            "resolved": resolved,                          # Leave single image
            "changed": changed,                            # Trigger ignore/must/cannot
            "kept_raw": len(raw_groups) - ignored - resolved,
            "final": len(view_groups)                      # Result groups len >=2）
        }
        return view_groups, summary
    
    # Clear relate entry
    def clear_constraints_for_group(self, grp: List[str]):
        s = set(m.lower() for m in grp)

        # Clear must/cannot pairs
        self.must_pairs = {p for p in self.must_pairs if not (p[0] in s and p[1] in s)}
        self.cannot_pairs = {p for p in self.cannot_pairs if not (p[0] in s and p[1] in s)}

        # Clear ignored files
        self.ignored_files -= s

    # Delete entry
    def remove_paths(self, deleted_paths: Iterable[str]) -> int:
        deleted = set(deleted_paths)

        # For must/cannot pair
        def _prune_pairs(s: Set[Pair]) -> int:
            before = len(s)
            s -= {p for p in s if (p[0] in deleted or p[1] in deleted)}
            return before - len(s)

        # For ignored_files
        def _prune_files(s: Set[str]) -> int:
            before = len(s)
            s -= (s & deleted)
            return before - len(s)

        removed = (
            _prune_pairs(self.must_pairs)
            + _prune_pairs(self.cannot_pairs)
            + _prune_files(self.ignored_files)
        )
        return removed
