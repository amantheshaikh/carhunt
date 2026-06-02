import { describe, it, expect } from "vitest";
import {
  getDrivetrain,
  getPowertrain,
  getDrivetrainTag,
  getPowertrainTag,
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
});
