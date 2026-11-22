import { describe, expect, it } from "vitest";
import { baseName, dirName, fmtBytes, fmtDuration, fmtSaved } from "./format";

describe("fmtBytes", () => {
  it("formats zero bytes", () => {
    expect(fmtBytes(0)).toBe("0 B");
  });

  it("formats bytes below one kilobyte", () => {
    expect(fmtBytes(999)).toBe("999 B");
  });

  it("formats exactly one kilobyte", () => {
    expect(fmtBytes(1000)).toBe("1.00 KB");
  });

  it("formats megabytes", () => {
    expect(fmtBytes(1.5e6)).toBe("1.50 MB");
  });
});

describe("fmtSaved", () => {
  it("prefixes positive savings with a minus sign", () => {
    expect(fmtSaved(1000)).toBe("\u22121.00 KB");
  });

  it("prefixes negative savings with a plus sign", () => {
    expect(fmtSaved(-1000)).toBe("+1.00 KB");
  });
});

describe("fmtDuration", () => {
  it("formats minutes and seconds", () => {
    expect(fmtDuration(65)).toBe("1:05");
  });

  it("formats hours, minutes and seconds", () => {
    expect(fmtDuration(3725)).toBe("1:02:05");
  });
});

describe("baseName", () => {
  it("handles forward slashes", () => {
    expect(baseName("/media/movies/film.mkv")).toBe("film.mkv");
  });

  it("handles backslashes", () => {
    expect(baseName("C:\\media\\movies\\film.mkv")).toBe("film.mkv");
  });
});

describe("dirName", () => {
  it("handles forward slashes", () => {
    expect(dirName("/media/movies/film.mkv")).toBe("/media/movies");
  });

  it("handles backslashes", () => {
    expect(dirName("C:\\media\\movies\\film.mkv")).toBe("C:\\media\\movies");
  });
});
