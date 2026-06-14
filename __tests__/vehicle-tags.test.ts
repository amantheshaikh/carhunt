import { describe, it, expect } from "vitest";
import {
  getDrivetrain,
  getPowertrain,
  getDrivetrainTag,
  getPowertrainTag,
  getValueScore,
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
  });
});
