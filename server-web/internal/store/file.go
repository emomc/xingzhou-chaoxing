package store

import "os"

func stat(path string) (os.FileInfo, error) {
	return os.Stat(path)
}
