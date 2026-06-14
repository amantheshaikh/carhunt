import { describe, it, expect } from "vitest";
import {
  getDrivetrain,
  getPowertrain,
  getDrivetrainTag,
  getPowertrainTag,
  getValueScore,
  getDealCategory,
  getDealCategoryStyle,
} from "../lib/vehicle-tags";

describe("getDrivetrain", () => {
  it("detects AWD from trim", () => {
    expect(getDrivetrain("SEL Convenience AWD", null)).toBe("AWD");
  });
  it("detects 4Motion as AWD", () => {
    expect(getDrivetrain("SEL R-Line 4Motion", "Tiguan")).toBe("AWD");
  });
  it("detects FWD", () => {
    expect(getDrivetrain("SE FWD Trim", null)).toBe("FWD");
  });
  it("returns null when no drivetrain keyword", () => {
    expect(getDrivetrain("SE R-Line Black", "Tiguan")).toBeNull();
  });
  it("handles null trim and model", () => {
    expect(getDrivetrain(null, null)).toBeNull();
  });
  it("checks model string too", () => {
    expect(getDrivetrain(null, "Tiguan 4Motion")).toBe("AWD");
  });
  it("prioritizes database resolved drivetrain", () => {
    expect(getDrivetrain("SE R-Line Black", "Tiguan", "awd")).toBe("AWD");
    expect(getDrivetrain("SEL AWD", null, "fwd")).toBe("FWD");
  });
  it("detects specialized keywords client-side", () => {
    expect(getDrivetrain("xDrive30i", null)).toBe("AWD");
    expect(getDrivetrain("sDrive28i", null)).toBe("FWD");
    expect(getDrivetrain("quattro", null)).toBe("AWD");
    expect(getDrivetrain("4MATIC", null)).toBe("AWD");
  });
});

describe("getPowertrain", () => {
  it("detects Hybrid from model name", () => {
    expect(getPowertrain("SEL Convenience", "Tucson Hybrid")).toBe("Hybrid");
  });
  it("detects Hybrid from trim", () => {
    expect(getPowertrain("Hybrid Limited", null)).toBe("Hybrid");
  });
  it("detects PHEV from plug-in keyword", () => {
    expect(getPowertrain("Plug-In Hybrid SEL", null)).toBe("PHEV");
  });
  it("returns null for gas vehicles", () => {
    expect(getPowertrain("SE R-Line Black", "Tiguan")).toBeNull();
  });
  it("handles null inputs", () => {
    expect(getPowertrain(null, null)).toBeNull();
  });
  it("detects PHEV from Volvo Recharge and T8", () => {
    expect(getPowertrain("Recharge T8 Plus Dark Theme eAWD", "XC60 Recharge")).toBe("PHEV");
  });
  it("detects Hybrid from Lexus 350h/400h/500h", () => {
    expect(getPowertrain("350h Premium", "NX 350h")).toBe("Hybrid");
  });
  it("detects Electric from EV/IONIQ", () => {
    expect(getPowertrain("IONIQ 5 SEL", null)).toBe("Electric");
  });
});

describe("getDrivetrainTag", () => {
  it("returns 'awd' for AWD vehicles", () => {
    expect(getDrivetrainTag("SEL AWD", null)).toBe("awd");
  });
  it("returns 'fwd' for FWD vehicles", () => {
    expect(getDrivetrainTag("SE FWD", null)).toBe("fwd");
  });
  it("returns empty string when drivetrain unknown", () => {
    expect(getDrivetrainTag("SE R-Line Black", null)).toBe("");
  });
  it("uses database resolved drivetrain", () => {
    expect(getDrivetrainTag("SE R-Line Black", null, "awd")).toBe("awd");
  });
});

describe("getPowertrainTag", () => {
  it("returns 'hybrid' for hybrids", () => {
    expect(getPowertrainTag("SEL", "Tucson Hybrid")).toBe("hybrid");
  });
  it("returns 'hybrid' for PHEVs", () => {
    expect(getPowertrainTag("Plug-In Hybrid", null)).toBe("hybrid");
  });
  it("returns 'gas' for non-hybrids", () => {
    expect(getPowertrainTag("SE R-Line Black", "Tiguan")).toBe("gas");
  });
  it("returns 'hybrid' for Volvo Recharge and Lexus 350h", () => {
    expect(getPowertrainTag("Recharge T8", null)).toBe("hybrid");
    expect(getPowertrainTag("350h Premium", null)).toBe("hybrid");
  });
});

describe("getValueScore", () => {
  it("computes percentage fallback score when no residualStd is provided", () => {
    expect(getValueScore(30000, 30000)).toBe(50);
    const score = getValueScore(27000, 30000);
    expect(score).toBeGreaterThanOrEqual(72);
    expect(score).toBeLessThanOrEqual(78);
  });

  it("computes z-score based score when residualStd is provided", () => {
    expect(getValueScore(27000, 30000, 1000)).toBe(94);
    expect(getValueScore(27000, 30000, 200)).toBe(99);
  });

  it("returns null for invalid inputs", () => {
    expect(getValueScore(null, 30000)).toBeNull();
    expect(getValueScore(30000, null)).toBeNull();
    expect(getValueScore(30000, 0)).toBeNull();
    expect(getValueScore(0, 30000)).toBeNull();
  });

  it("limits residualStd to a minimum floor of 600", () => {
    // Both 500 and 600 should yield the exact same score due to Math.max(600.0, residualStd)
    const scoreWith500 = getValueScore(27000, 30000, 500);
    const scoreWith600 = getValueScore(27000, 30000, 600);
    expect(scoreWith500).toBe(scoreWith600);
    expect(scoreWith500).toBe(99); // 100 / (1 + exp(-3000/600 * 0.94)) = 100 / (1 + exp(-4.7)) = 100 / (1 + 0.009) approx 99. wait, let's verify z = 3000 / 600 = 5. 5 * 0.94 = 4.7. Math.round(100 / (1 + exp(-4.7))) = 99. Wait, yes, the score is indeed 99.
  });
});

describe("getDealCategory", () => {
  it("returns null if score is null", () => {
    expect(getDealCategory(null)).toBeNull();
  });
  it("returns Exceptional Deal for score >= 88", () => {
    expect(getDealCategory(88)).toBe("Exceptional Deal");
    expect(getDealCategory(95)).toBe("Exceptional Deal");
  });
  it("returns Great Deal for score 72 to 87", () => {
    expect(getDealCategory(72)).toBe("Great Deal");
    expect(getDealCategory(87)).toBe("Great Deal");
  });
  it("returns Good Deal for score 58 to 71", () => {
    expect(getDealCategory(58)).toBe("Good Deal");
    expect(getDealCategory(71)).toBe("Good Deal");
  });
  it("returns Fair Price for score 45 to 57", () => {
    expect(getDealCategory(45)).toBe("Fair Price");
    expect(getDealCategory(57)).toBe("Fair Price");
  });
  it("returns Poor Value for score < 45", () => {
    expect(getDealCategory(44)).toBe("Poor Value");
    expect(getDealCategory(0)).toBe("Poor Value");
  });
});

describe("getDealCategoryStyle", () => {
  it("returns style for null score", () => {
    expect(getDealCategoryStyle(null)).toBe("bg-zinc-700 text-zinc-400");
  });
  it("returns style for Exceptional Deal", () => {
    expect(getDealCategoryStyle(88)).toContain("bg-emerald-900");
  });
  it("returns style for Great Deal", () => {
    expect(getDealCategoryStyle(72)).toContain("bg-green-900");
  });
  it("returns style for Good Deal", () => {
    expect(getDealCategoryStyle(58)).toContain("bg-blue-900");
  });
  it("returns style for Fair Price", () => {
    expect(getDealCategoryStyle(45)).toContain("bg-zinc-800");
  });
  it("returns style for Poor Value", () => {
    expect(getDealCategoryStyle(40)).toContain("bg-red-950");
  });
});

describe("getDrivetrain extra keywords", () => {
  it("detects AWD from database 4wd", () => {
    expect(getDrivetrain(null, null, "4wd")).toBe("AWD");
  });
  it("detects FWD from database rwd", () => {
    expect(getDrivetrain(null, null, "rwd")).toBe("FWD");
  });
  it("falls back to text search if database drivetrain is unknown", () => {
    expect(getDrivetrain("AWD trim", null, "unknown")).toBe("AWD");
  });
  it("detects AWD from keywords", () => {
    expect(getDrivetrain("4x4 trim", null)).toBe("AWD");
    expect(getDrivetrain("all wheel drive", null)).toBe("AWD");
    expect(getDrivetrain("eAWD", null)).toBe("AWD");
    expect(getDrivetrain("X3 M40i", null)).toBe("AWD");
    expect(getDrivetrain("X1 M35i", null)).toBe("AWD");
    expect(getDrivetrain("T6 B6", null)).toBe("AWD");
  });
  it("detects FWD from keywords", () => {
    expect(getDrivetrain("front wheel drive", null)).toBe("FWD");
    expect(getDrivetrain("2wd", null)).toBe("FWD");
    expect(getDrivetrain("B4 Mild Hybrid", null)).toBe("FWD");
  });
});

describe("getPowertrain extra keywords", () => {
  it("detects PHEV from keywords", () => {
    expect(getPowertrain("plug in hybrid", null)).toBe("PHEV");
    expect(getPowertrain("T8", null)).toBe("PHEV");
  });
  it("detects Hybrid from keywords", () => {
    expect(getPowertrain("400h", null)).toBe("Hybrid");
    expect(getPowertrain("500h", null)).toBe("Hybrid");
  });
  it("detects Electric from keywords", () => {
    expect(getPowertrain("bev", null)).toBe("Electric");
    expect(getPowertrain("ev", null)).toBe("Electric");
  });
});

