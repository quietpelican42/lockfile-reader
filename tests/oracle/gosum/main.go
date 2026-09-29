// Copyright 2009 The Go Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license (see LICENSE-go).
//
// Test oracle for go.sum. readGoSum is copied from cmd/go/internal/modfetch/fetch.go
// in the Go toolchain; the rest is a small driver. Not part of the shipped package.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type Version struct{ Path, Version string }

const emptyGoModHash = "h1:G7mAYYxgmS0lVkHyy2hEOLQCFB0DlQFTMLWggykrydY="

func readGoSum(dst map[Version][]string, data []byte) error {
	lineno := 0
	for len(data) > 0 {
		var line []byte
		lineno++
		i := bytes.IndexByte(data, '\n')
		if i < 0 {
			line, data = data, nil
		} else {
			line, data = data[:i], data[i+1:]
		}
		f := strings.Fields(string(line))
		if len(f) == 0 {
			continue
		}
		if len(f) != 3 {
			return fmt.Errorf("line %d: wrong number of fields %v", lineno, len(f))
		}
		if f[2] == emptyGoModHash {
			continue
		}
		mod := Version{Path: f[0], Version: f[1]}
		dst[mod] = append(dst[mod], f[2])
	}
	return nil
}

func main() {
	dir, out := os.Args[1], os.Args[2]
	os.MkdirAll(out, 0o755)
	ents, _ := os.ReadDir(dir)
	ok, bad := 0, 0
	for _, e := range ents {
		data, _ := os.ReadFile(filepath.Join(dir, e.Name()))
		m := map[Version][]string{}
		if err := readGoSum(m, data); err != nil {
			bad++
			continue
		}
		res := map[string][]string{}
		for k, v := range m {
			res[k.Path+" "+k.Version] = v
		}
		b, _ := json.Marshal(res)
		os.WriteFile(filepath.Join(out, e.Name()+".json"), b, 0o644)
		ok++
	}
	fmt.Println("gosum ok", ok, "fail", bad)
}
