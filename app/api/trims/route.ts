import { NextRequest, NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

interface Vehicle {
  make: string;
  model: string;
  trims: string[];
  msrp?: Record<string, Record<string, number>>; // year → trim → msrp
}

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const make  = searchParams.get("make")  ?? "";
  const model = searchParams.get("model") ?? "";
  const year  = searchParams.get("year")  ?? "";

  const carsJson = JSON.parse(
    readFileSync(join(process.cwd(), "agents/cars.json"), "utf-8")
  ) as { vehicles: Vehicle[] };

  const vehicle = carsJson.vehicles.find(
    (v) => v.make.toLowerCase() === make.toLowerCase() &&
            v.model.toLowerCase() === model.toLowerCase()
  );

  // MSRP for the requested year (fall back to nearest year if exact not found)
  let msrpByTrim: Record<string, number> | null = null;
  if (vehicle?.msrp && year) {
    const yearData = vehicle.msrp[year];
    if (yearData) {
      msrpByTrim = yearData;
    } else {
      // Nearest year fallback
      const years = Object.keys(vehicle.msrp).map(Number).sort();
      const y = Number(year);
      const nearest = years.reduce((a, b) => Math.abs(b - y) < Math.abs(a - y) ? b : a);
      msrpByTrim = vehicle.msrp[String(nearest)] ?? null;
    }
  }

  return NextResponse.json({
    trims: vehicle?.trims ?? [],
    msrp: msrpByTrim,   // { "EX": 30390, "SX": 35590 } or null
  });
}
